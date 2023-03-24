import os
import json
import openai
import logging
import asyncio
from chalice import Chalice
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import boto3

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Load the API tokens from the .env file
load_dotenv()

TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ADMIN_ID = os.getenv('BOT_ADMIN_USER_ID')
DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE_NAME')
DYNAMODB_USER_TABLE = os.getenv('DYNAMODB_USERS_TABLE_NAME')

APP_NAME = "gpt-telegram-bot"
LAMBDA_MESSAGE_HANDLER = "lambda-message-handler"
ADMIN_USER_KEY = "admin_user"
TYPE_ITEM_MESSAGE = "message"
TYPE_ITEM_USER = "allowed_user"


# Initialize the OpenAI library
openai.api_key = OPENAI_API_KEY
app = Chalice(app_name=APP_NAME)
dynamodb =  boto3.resource('dynamodb')
messages_table = dynamodb.Table(DYNAMODB_TABLE)
users_table = dynamodb.Table(DYNAMODB_USER_TABLE)

################# DYNAMO DB DATA PROCESSING #############################
# Users
def allowed_only(user_id, user_role):
    print("Trying to get users from the table: " + str(user_id) + " " + str(user_role))
    response = users_table.get_item(
        Key={'user_id': str(user_id),
             'user_type': str(user_role)
        }
    )
    return 'Item' in response

def admin_user(user_id):
    return allowed_only(user_id, ADMIN_USER_KEY)

def allowed_user(user_id):
    return allowed_only(user_id, TYPE_ITEM_USER)

def _add_allowed_user(user_id, user_role):
    users_table.put_item(
            Item={
                'user_id': str(user_id),
                'user_type': str(user_role)
            }
        )

# Messages
def store_message(user_id, message_id, role, text):
    messages_table.put_item(
        Item={
            'user_id': str(user_id),
            'message_id': str(message_id),
            'role': role,
            'text': text
        }
    )

def delete_messages(user_id):
    messages = get_messages(user_id)
    with messages_table.batch_writer() as batch:
        for msg in messages:
            batch.delete_item(
                Key={
                    'user_id': str(user_id),
                    'message_id': str(msg['message_id'])
                }
            )

def get_messages(user_id):
    response = messages_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('user_id').eq(str(user_id))
    )
    return response['Items']

######################## CHAT GPT MESSAGES PROCESSING ################################
# Function to get a response from ChatGPT-3.5
def get_chatgpt_response(prompt, chat_context):
    chat_context.append({"role": "user", "content": prompt})
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0301",
        messages=chat_context
    )
    response_text = response.choices[0].message.content.strip()
    return response_text

def get_formatted_messages_for_gpt(user_id):
    return [{"role": msg["role"], "content": msg["text"]} for msg in get_messages(user_id)]

################# GENERAL COMMANDS ##########################
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text('Welcome to the ChatGPT-3.5 bot! Type your message and I will respond.\nUse /clear to clear discussion context')

################# USERS ACTIONS ##############################
async def add_user(update: Update, context: CallbackContext):
    if admin_user(str(update.message.from_user.id)):
        _add_allowed_user(context.args[0], TYPE_ITEM_USER)
    else:
        await update.message.reply_text('You should be an Admin to perform this operation')
    await users(update, context)

async def delete_user(update: Update, context: CallbackContext):
    if admin_user(str(update.message.from_user.id)):
        user_id = context.args[0]
        users_table.delete_item(Key={'user_id': user_id, 'user_type': TYPE_ITEM_USER})
        await update.message.reply_text("User " + user_id + " deleted!")
    else:
        await update.message.reply_text('You should be an Admin to perform this operation')
    await users(update, context)

async def users(update: Update, context: CallbackContext):
    if allowed_user(str(update.message.from_user.id)):
        response = users_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('user_type').eq(TYPE_ITEM_USER)
        )
        allowed_users = [item['user_id'] for item in response['Items']]
        response_strings = ""
        for i, user in enumerate(allowed_users):
            response_strings += str(i+1) + " " + str(user) + "\n"
        await update.message.reply_text(response_strings)
    
#################### MESSAGE PROCESSING ###########################
async def handle_text(update: Update, context: CallbackContext):
    user_text = update.message.text
    user_id = str(update.message.from_user.id)
    store_message(user_id, update.message.id, "user", user_text)
    if allowed_user(user_id):
        chat_context = get_formatted_messages_for_gpt(user_id)
        chatgpt_response = get_chatgpt_response(user_text, chat_context)
        store_message(update.message.from_user.id, str(update.message.id) + "a", "assistant", chatgpt_response)
        await update.message.reply_text(text=chatgpt_response)
    else:
        await update.message.reply_text("You don't have permissions to use that bot!")

async def clear(update: Update, context: CallbackContext):
    delete_messages(update.message.from_user.id)
    logger.info("Context should be cleared by now")
    await update.message.reply_text('Context cleared')

###################### MAIN ##########################################
@app.lambda_function(name=LAMBDA_MESSAGE_HANDLER)
def message_handler(event, context):
    _add_allowed_user(ADMIN_ID, ADMIN_USER_KEY)
    _add_allowed_user(ADMIN_ID, TYPE_ITEM_USER)
    return asyncio.run(run_bot_application(event))

async def run_bot_application(event):
    application = Application.builder().token(TELEGRAM_API_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('clear', clear))
    application.add_handler(CommandHandler('add_user', add_user))
    application.add_handler(CommandHandler('users', users))
    application.add_handler(CommandHandler('delete_user', delete_user))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Registered all the handlers")
    print("Try to initialise")
    await application.initialize()
    print("Got something from Telegram: " + str(event))
    try:
        print("Trying to process the update")
        if "message" in event["body"]:
            print("Preparing the update")
            update = Update.de_json(data=json.loads(event["body"]), bot=application.bot)
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
