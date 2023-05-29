import math
import os
import io
import time
import datetime
import json
import openai
import logging
import asyncio
import boto3
from pydub import AudioSegment
from chalice import Chalice
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("BOT_ADMIN_USER_ID")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE_NAME")
DYNAMODB_USER_TABLE = os.getenv("DYNAMODB_USERS_TABLE_NAME")
DYNAMODB_CONFIG_TABLE = os.getenv("DYNAMODB_CONFIG_TABLE_NAME")
DYNAMODB_SPENDINGS_TABLE = os.getenv("DYNAMODB_SPENDINGS_TABLE_NAME")

APP_NAME = "gpt-telegram-bot"
LAMBDA_MESSAGE_HANDLER = "lambda-message-handler"
ADMIN_USER_KEY = "admin_user"
TYPE_ITEM_MESSAGE = "message"
TYPE_ITEM_USER = "allowed_user"
CONTEXTS_FOLDER = "chalicelib"
NUMBER_OF_PREDEFINED_CONTEXT_MESSAGES = 1  # TODO: Will be dynamic
PERMISSION_ERROR_TEXT = "You don't have permissions to use that bot!"

CALLBACK_CORRECT_TRANSCRIPT = "correct_transcript"
CALLBACK_WRONG_TRANSCRIPT = "wrong_transcript"
VOICE_PROCESSING_KEYBOARD = [[InlineKeyboardButton(text="Correct! Send it to GPT!", callback_data=CALLBACK_CORRECT_TRANSCRIPT)],
                             [InlineKeyboardButton(text="No, I'll copy and edit myself", callback_data=CALLBACK_WRONG_TRANSCRIPT)]]

MODELS = {"gpt3": {"model": "gpt-3.5-turbo", "request_price": 2, "response_price": 2},
          "gpt4": {"model": "gpt-4", "response_price": 60, "request_price": 20}}
IMAGE_MODELS = {"dall-e": {"model": "dall-e", "response_price": 20}}
VOICE_MODELS = {"whisper": {"model": "whisper-1", "price_per_minute": 6}}
DEFAULT_WHISPER_MODEL = "whisper-1"


# Initialize the OpenAI library
openai.api_key = OPENAI_API_KEY
# Initialize the Chalice app
app = Chalice(app_name=APP_NAME)
# Connect to DynamoDB
dynamodb = boto3.resource("dynamodb")
messages_table = dynamodb.Table(DYNAMODB_TABLE)
users_table = dynamodb.Table(DYNAMODB_USER_TABLE)
config_table = dynamodb.Table(DYNAMODB_CONFIG_TABLE)
spendings_table = dynamodb.Table(DYNAMODB_SPENDINGS_TABLE)


################# OTHER FUNCTIONS #############################
def get_price(tokens, user_id):
    model = get_config(user_id)
    return tokens["completion_tokens"] * model["response_price"] / 1000 / 1000 + tokens["prompt_tokens"] * model["request_price"] / 1000 / 1000


# Context
def load_contexts(user_id):
    filenames = get_json_filenames(CONTEXTS_FOLDER)
    for file in filenames:
        for i, context_line in enumerate(json_from_file(file)["context"]):
            store_message(
                user_id, i, context_line["role"], context_line["content"])


def get_json_filenames(folder_path):
    json_files = [f for f in os.listdir(folder_path) if f.endswith(".json")]
    return json_files


def json_from_file(name):
    filename = os.path.join(
        os.path.dirname(__file__), "chalicelib", name)
    with open(filename) as f:
        return json.load(f)


################# DYNAMO DB DATA PROCESSING #############################
# Users
def allowed_only(user_id, user_role):
    response = users_table.get_item(
        Key={"user_id": str(user_id),
             "user_type": str(user_role)
             }
    )
    return "Item" in response


def admin_user(user_id):
    return allowed_only(user_id, ADMIN_USER_KEY)


def allowed_user(user_id):
    return allowed_only(user_id, TYPE_ITEM_USER)


def _add_allowed_user(user_id, user_role):
    users_table.put_item(
        Item={
            "user_id": str(user_id),
            "user_type": str(user_role)
        }
    )
    if user_role == TYPE_ITEM_USER:
        load_contexts(user_id)


# Messages
def store_message(user_id, message_id, role, text, tokens_used={}):
    messages_table.put_item(
        Item={
            "user_id": str(user_id),
            "message_id": int(message_id),
            "role": role,
            "text": text,
            "tokens_used": tokens_used
        }
    )


def delete_messages(user_id):
    messages = get_messages(user_id)
    with messages_table.batch_writer() as batch:
        for msg in messages:
            if int(msg["message_id"]) > NUMBER_OF_PREDEFINED_CONTEXT_MESSAGES:
                batch.delete_item(
                    Key={
                        "user_id": str(user_id),
                        "message_id": int(msg["message_id"])
                    }
                )


def get_messages(user_id):
    response = messages_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key(
            "user_id").eq(str(user_id))
    )
    return response["Items"]


def get_message_by_id(user_id, message_id):
    response = messages_table.get_item(
        Key={
            "user_id": str(user_id),
            "message_id": int(message_id)
        }
    )
    if "Item" in response:
        item = response["Item"]
        return item["text"]
    else:
        raise Exception("Item is not found in DynamoDB")


# Config
def get_config(user_id):
    response = config_table.get_item(
        Key={
            "user_id": str(user_id)
        }
    )
    if "Item" in response:
        return response["Item"]
    return None


def is_config_present(user_id):
    response = get_config(user_id)
    if response:
        return True
    return False


def create_initial_config(user_id, model):
    config_table.put_item(
        Item={
            "user_id": str(user_id),
            "model": model["model"],
            "request_price": model["request_price"],
            "response_price": model["response_price"]
        }
    )


def update_config(user_id, model):
    config_table.update_item(
        Key={
            "user_id": str(user_id)
        },
        UpdateExpression="set model=:m, request_price=:r, response_price=:rp",
        ExpressionAttributeValues={
            ":m": model["model"],
            ":r": model["request_price"],
            ":rp": model["response_price"]
        },
        ReturnValues="UPDATED_NEW"
    )


def delete_config(user_id):
    config_table.delete_item(
        Key={
            "user_id": str(user_id)
        }
    )


# Spendings
def add_spending(user_id, tokens_spent, model_name):
    timestamp = int(time.time())
    spendings_table.put_item(
        Item={
            "user_id": str(user_id),
            "timestamp": timestamp,
            "completion_tokens": int(tokens_spent["completion_tokens"]),
            "prompt_tokens": int(tokens_spent["prompt_tokens"]),
            "price_in_10th_of_cents": math.ceil(get_price(tokens_spent, user_id) * 1000),
            "model_name": model_name,
            "human_readable_time": datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        }
    )


def add_image_voice_spending(user_id, price, model_name):
    timestamp = int(time.time())
    spendings_table.put_item(
        Item={
            "user_id": str(user_id),
            "timestamp": timestamp,
            "price_in_10th_of_cents": price,
            "model_name": model_name,
            "human_readable_time": datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        }
    )


def get_spendings_for_user(user_id):
    sum = 0
    spending_items = spendings_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key(
            "user_id").eq(str(user_id))
    )["Items"]
    for item in spending_items:
        sum += item["price_in_10th_of_cents"]
    return sum / 1000


def get_all_spendings():
    spending_items = spendings_table.scan()["Items"]
    spendings_by_user = {}
    for item in spending_items:
        user_id = item['user_id']
        spendings = item['price_in_10th_of_cents']

        if user_id not in spendings_by_user:
            spendings_by_user[user_id] = 0

        spendings_by_user[user_id] += spendings
    return spendings_by_user


######################## CHAT GPT MESSAGES PROCESSING ################################
# Function to get a response from ChatGPT
def get_chatgpt_response(prompt, chat_context, model_name="gpt-3.5-turbo"):
    print("User asked: " + prompt)
    print("Actual model used: " + model_name)
    chat_context.append({"role": "user", "content": prompt})
    response = openai.ChatCompletion.create(
        model=model_name,
        messages=chat_context
    )
    print("Model from OpenAI response: " + response.model)
    response_text = response.choices[0].message.content.strip()
    prompt_tokens = response.usage.prompt_tokens
    completion_tokens = response.usage.completion_tokens
    total_tokens = response.usage.total_tokens
    print("Total tokens: " + str(total_tokens) + " Prompt tokens: " +
          str(prompt_tokens) + " Completion tokens: " + str(completion_tokens))
    return response_text, {"total_tokens": total_tokens, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def process_text(user_text, user_id, message_id):
    chat_context = get_formatted_messages_for_gpt(user_id)
    model_name = get_config(user_id)["model"]
    print("Model will be used: " + model_name)
    response, tokens = get_chatgpt_response(
        user_text, chat_context, model_name)
    add_spending(user_id, tokens, model_name)
    store_message(user_id, int(message_id) + 1, "assistant", response, tokens)
    # if "image:" in response:
    #     prompt = response.split(":")[1].split("\"")[0]
    #     response = prompt + "\n" + get_generated_image(prompt)
    return response, tokens


def get_formatted_messages_for_gpt(user_id):
    return [{"role": msg["role"], "content": msg["text"]} for msg in get_messages(user_id)]


# Image processing
def get_generated_image(prompt, number_of_pictures=1, size="1024x1024"):
    response = openai.Image.create(
        prompt=prompt,
        n=number_of_pictures,
        size=size
    )
    return response["data"][0]["url"]


# Voice processing
def transcribe(ogg_audio_bytes):
    mp3_bytes = convert_ogg_to_mp3(ogg_audio_bytes)
    mp3_bytes.name = "filename.mp3"  # required by transcribe method
    return openai.Audio.transcribe(model=VOICE_MODELS["whisper"]["model"], file=mp3_bytes)["text"]


def convert_ogg_to_mp3(ogg_bytes):
    audio_file = io.BytesIO(ogg_bytes)
    # Read the OGG file from the message into an AudioSegment object
    audio_data = AudioSegment.from_ogg(audio_file)
    # Convert the audio to MP3 format
    mp3_data = io.BytesIO(audio_data.export(format="mp3").read())
    return mp3_data


################# USERS ACTIONS ##############################
# /start handler
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Welcome to the ChatGPT-3.5 bot! Type your message and I will respond.\nUse /clear to clear discussion context")


# /add_user handler
async def add_user(update: Update, context: CallbackContext):
    if admin_user(str(update.message.from_user.id)):
        _add_allowed_user(context.args[0], TYPE_ITEM_USER)
        create_initial_config(context.args[0], MODELS["gpt3"])
    else:
        await update.message.reply_text("You should be an Admin to perform this operation")
    await users(update, context)


# /delete_user handler
async def delete_user(update: Update, context: CallbackContext):
    if admin_user(str(update.message.from_user.id)):
        user_id = context.args[0]
        users_table.delete_item(
            Key={"user_id": user_id, "user_type": TYPE_ITEM_USER})
        config_table.delete_item(Key={"user_id": user_id})
        await update.message.reply_text("User " + user_id + " deleted!")
    else:
        await update.message.reply_text("You should be an Admin to perform this operation")
    await users(update, context)


# /users handler
async def users(update: Update, context: CallbackContext):
    if allowed_user(str(update.message.from_user.id)):
        response = users_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr(
                "user_type").eq(TYPE_ITEM_USER)
        )
        allowed_users = [item["user_id"] for item in response["Items"]]
        response_strings = ""
        for i, user in enumerate(allowed_users):
            response_strings += str(i+1) + " " + str(user) + "\n"
        await update.message.reply_text(response_strings)
    else:
        await update.message.reply_text()


#################### MESSAGE PROCESSING ###########################
# Text message handler
async def handle_text(update: Update, context: CallbackContext):
    user_text = update.message.text
    user_id = str(update.message.from_user.id)
    store_message(user_id, update.message.id, "user", user_text)
    if allowed_user(user_id):
        response_text, tokens = process_text(
            user_text, user_id, update.message.id)
        await update.message.reply_text(text=response_text,  parse_mode=ParseMode.MARKDOWN)
        print("Price: " + str(get_price(tokens, user_id)))
        await update.message.reply_text(text="Last request used " + str(tokens["total_tokens"]) + " tokens. It costed " + str(get_price(tokens, user_id)) + " USD")
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# Voice message handler
async def voice_to_text(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if allowed_user(user_id):
        voice = update.message.voice
        duration = voice.duration
        # Assuming `voice_message` is a Telegram `Voice` message object
        audio_file = voice.file_id
        # Download the audio file from Telegram servers
        file = await context.bot.get_file(audio_file)
        audio_data = await file.download_as_bytearray()
        text = transcribe(audio_data)
        processing_cost = math.ceil(duration * VOICE_MODELS["whisper"]["price_per_minute"] / 60)
        add_image_voice_spending(
            user_id, processing_cost, VOICE_MODELS["whisper"]["model"])
        reply_markup = InlineKeyboardMarkup(VOICE_PROCESSING_KEYBOARD)
        await update.message.reply_text(text + "\n" + "Is that what you told? \n Processing costed: " + str(processing_cost / 1000 / 60) + " USD", reply_markup=reply_markup)
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# /clear handler
async def clear(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if allowed_user(user_id):
        delete_messages(update.message.from_user.id)
        logger.info("Context should be cleared by now")
        await update.message.reply_text("Context cleared")
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# /image handler
async def generate_image(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if allowed_user(user_id):
        prompt = " ".join(context.args).strip()
        if prompt:
            url = get_generated_image(prompt)
            await update.message.reply_html(url)
            add_image_voice_spending(
                user_id, IMAGE_MODELS["dall-e"]["response_price"], IMAGE_MODELS["dall-e"]["model"])
            await update.message.reply_text("Image generation costed:" + str(IMAGE_MODELS["dall-e"]["response_price"]/1000) + " USD")
        else:
            await update.message.reply_text("Please provide the text prompt")
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# /model handler
async def choose_model(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if admin_user(user_id):
        model = get_config(user_id)
        if len(context.args) == 0:
            await update.message.reply_text("Your current model is: " + model["model"])
        else:
            new_model_name = context.args[0]
            if new_model_name in MODELS.keys():
                await update.message.reply_text("Model changed to " + new_model_name)
                update_config(user_id, MODELS[new_model_name])
            else:
                await update.message.reply_text("Model not found \n Your current model: " + get_config(user_id)["model"])
    else:
        await update.message.reply_text("Your current model is: " + get_config(user_id)["model"] + "\n" + PERMISSION_ERROR_TEXT)


# spendings handler
async def get_total_spending(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    spending = get_spendings_for_user(user_id)
    await update.message.reply_text("Your total spendings: " + str(spending) + " USD")


# all spendings handler
async def get_all_users_spending(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if user_id == ADMIN_ID:
        spendings_by_user = get_all_spendings()
        for user_id, total_spendings in spendings_by_user.items():
            await update.message.reply_text(f"{user_id}: total spendings {total_spendings / 1000} USD")
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# Callback handler
async def process_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    print("Inline message id: " + str(message_id))
    message = query.message.text.splitlines()[0]
    await query.answer()
    await query.edit_message_text(message)

    if query.data == CALLBACK_CORRECT_TRANSCRIPT:
        response, tokens_used = process_text(message, user_id, message_id)
        await context.bot.send_message(chat_id=chat_id, text=response)
        await context.bot.send_message(chat_id=chat_id, text="Last request used " + str(tokens_used["total_tokens"]) + " tokens. It costed " + str(get_price(tokens_used, user_id)) + " USD")
    elif query.data == CALLBACK_WRONG_TRANSCRIPT:
        pass


###################### MAIN ##########################################
@app.lambda_function(name=LAMBDA_MESSAGE_HANDLER)
def message_handler(event, context):
    if not is_config_present(ADMIN_ID):
        create_initial_config(ADMIN_ID, MODELS["gpt3"])
    _add_allowed_user(ADMIN_ID, ADMIN_USER_KEY)
    _add_allowed_user(ADMIN_ID, TYPE_ITEM_USER)
    return asyncio.run(run_bot_application(event))


async def run_bot_application(event):
    application = Application.builder().token(TELEGRAM_API_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("add_user", add_user))
    application.add_handler(CommandHandler("users", users))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("image", generate_image))
    application.add_handler(CommandHandler("model", choose_model))
    application.add_handler(CommandHandler("spendings", get_total_spending))
    application.add_handler(CommandHandler("spendings_all", get_all_users_spending))
    application.add_handler(MessageHandler(filters.VOICE, voice_to_text))
    application.add_handler(CallbackQueryHandler(process_callback))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text))
    print("Registered all the handlers")
    print("Try to initialise")
    await application.initialize()
    print("Got something from Telegram: " + str(event))
    try:
        print("Trying to process the update")
        if "message" in event["body"]:
            print("Preparing the update")
            update = Update.de_json(data=json.loads(
                event["body"]), bot=application.bot)
            await application.update_queue.put(update)
            print("Let's process the update: " + str(update))
            await application.process_update(update)
            print("Update processed")
        else:
            print("Not a message event")
    except Exception as e:
        logger.warning(e)
        print("Exception happened: " + e)
        return {"statusCode": 500}
    return {"statusCode": 200}
