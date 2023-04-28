import os
import io
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

APP_NAME = "gpt-telegram-bot"
LAMBDA_MESSAGE_HANDLER = "lambda-message-handler"
ADMIN_USER_KEY = "admin_user"
TYPE_ITEM_MESSAGE = "message"
TYPE_ITEM_USER = "allowed_user"
CONTEXTS_FOLDER = "chalicelib"
NUMBER_OF_PREDEFINED_CONTEXT_MESSAGES = 1 # TODO: Will be dynamic
PERMISSION_ERROR_TEXT = "You don't have permissions to use that bot!"

CALLBACK_CORRECT_TRANSCRIPT = "correct_transcript"
VOICE_PROCESSING_KEYBOARD = [[InlineKeyboardButton(text = "Correct! Send it to GPT!", callback_data=CALLBACK_CORRECT_TRANSCRIPT)]]

DEFAULT_GPT_MODEL = "gpt-4" # "gpt-3.5-turbo"
PRICE_PER_1000_TOKENS = 0.06
DEFAULT_WHISPER_MODEL = "whisper-1"


# Initialize the OpenAI library
openai.api_key = OPENAI_API_KEY
app = Chalice(app_name=APP_NAME)
dynamodb = boto3.resource("dynamodb")
messages_table = dynamodb.Table(DYNAMODB_TABLE)
users_table = dynamodb.Table(DYNAMODB_USER_TABLE)


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


# Context
def load_contexts(user_id):
    filenames = get_json_filenames(CONTEXTS_FOLDER)
    for file in filenames:
        for i, context_line in enumerate(json_from_file(file)["context"]):
            store_message(user_id, i, context_line["role"], context_line["content"])


def get_json_filenames(folder_path):
    json_files = [f for f in os.listdir(folder_path) if f.endswith(".json")]
    return json_files


def json_from_file(name):
    filename = os.path.join(
    os.path.dirname(__file__), "chalicelib", name)
    with open(filename) as f:
        return json.load(f)


# Messages
def store_message(user_id, message_id, role, text):
    messages_table.put_item(
        Item={
            "user_id": str(user_id),
            "message_id": int(message_id),
            "role": role,
            "text": text
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
            "user_id": user_id,
            "message_id": message_id
        }
    )
    if "Item" in response:
        item = response["Item"]
        return item["text"]
    else:
        raise Exception("Item is not found in DynamoDB")


######################## CHAT GPT MESSAGES PROCESSING ################################
# Function to get a response from ChatGPT-3.5
def get_chatgpt_response(prompt, chat_context):
    print(prompt)
    chat_context.append({"role": "user", "content": prompt})
    response = openai.ChatCompletion.create(
        model=DEFAULT_GPT_MODEL,
        messages=chat_context
    )
    response_text = response.choices[0].message.content.strip()
    tokens_used = response.usage.total_tokens
    return response_text, tokens_used


def process_text(user_text, user_id, message_id):
    chat_context = get_formatted_messages_for_gpt(user_id)
    response, tokens_used = get_chatgpt_response(user_text, chat_context)
    store_message(user_id, int(message_id) + 1, "assistant", response)
    if "image:" in response:
        prompt = response.split(":")[1].split("\"")[0]
        response = prompt + "\n" + get_generated_image(prompt)
    return response, tokens_used


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
    return openai.Audio.transcribe(model=DEFAULT_WHISPER_MODEL, file=mp3_bytes)["text"]


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
    else:
        await update.message.reply_text("You should be an Admin to perform this operation")
    await users(update, context)


# /delete_user handler
async def delete_user(update: Update, context: CallbackContext):
    if admin_user(str(update.message.from_user.id)):
        user_id = context.args[0]
        users_table.delete_item(
            Key={"user_id": user_id, "user_type": TYPE_ITEM_USER})
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
        response_text, tokens_used = process_text(user_text, user_id, update.message.id)
        await update.message.reply_text(text=response_text,  parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(text="Last request used " + str(tokens_used) + " tokens. It costed " + str(tokens_used * PRICE_PER_1000_TOKENS / 1000) + " USD")
    else:
        await update.message.reply_text(PERMISSION_ERROR_TEXT)


# Voice message handler
async def voice_to_text(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if allowed_user(user_id):
        voice = update.message.voice
        # Assuming `voice_message` is a Telegram `Voice` message object
        audio_file = voice.file_id
        # Download the audio file from Telegram servers
        file = await context.bot.get_file(audio_file)
        audio_data = await file.download_as_bytearray()
        text = transcribe(audio_data)
        reply_markup = InlineKeyboardMarkup(VOICE_PROCESSING_KEYBOARD)
        await update.message.reply_text(text + "\n" + "Is that what you told?", reply_markup=reply_markup)
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
        else:
            await update.message.reply_text("Please provide the text prompt")
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
        await context.bot.send_message(chat_id=chat_id, text="Last request used " + str(tokens_used) + " tokens. It costed " + str(tokens_used * 0.002 / 1000) + " USD")


###################### MAIN ##########################################
@app.lambda_function(name=LAMBDA_MESSAGE_HANDLER)
def message_handler(event, context):
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
