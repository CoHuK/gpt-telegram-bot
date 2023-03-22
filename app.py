import os
import json
import openai
import logging
import asyncio
from asgiref.sync import async_to_sync
from chalice import Chalice
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

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
ALLOWED_USER_IDS = [ADMIN_ID]
APP_NAME = "gpt-telegram-bot"
LAMBDA_MESSAGE_HANDLER = "lambda-message-handler"

# Initialize the OpenAI library
openai.api_key = OPENAI_API_KEY

async def allowed_only(update, allowed_list):
    user_id = update.message.from_user.id
    if str(user_id) not in allowed_list:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return False
    return True

async def admin_only(update):
    return await allowed_only(update, ADMIN_ID)

async def allowed_users_only(update):
    return await allowed_only(update, ALLOWED_USER_IDS)

# Function to start the bot
async def start(update: Update, context: CallbackContext):
    context.user_data["chat context"] = []
    await update.message.reply_text('Welcome to the ChatGPT-3.5 bot! Type your message and I will respond.\nUse /clear do clear discussion context')

async def clear(update: Update, context: CallbackContext):
    context.user_data["chat context"] = []
    logger.info("Context should be cleared by now: " + str(context.user_data["chat context"]))
    await update.message.reply_text('Context cleared')
  
async def add_user(update: Update, context: CallbackContext):
    if await admin_only(update):
        ALLOWED_USER_IDS.append(context.args[0])
        logger.info("User " + context.args[0] + " added to allow list")
        await users(update, context)

async def delete_user(update: Update, context: CallbackContext):
    if await admin_only(update):
        user_index = int(context.args[0])
        if 1 < user_index <= len(ALLOWED_USER_IDS):
            await update.message.reply_text("User " + ALLOWED_USER_IDS[user_index - 1] + " is deleted")
            del ALLOWED_USER_IDS[user_index - 1]
        else:
            await update.message.reply_text("Wrong user list index number. Use /users to get the right one.")
        await users(update, context)

async def users(update: Update, context: CallbackContext):
    if await allowed_users_only(update):
        response_strings = ""
        for i, user in enumerate(ALLOWED_USER_IDS):
            response_strings += str(i+1) + " " + str(user) + "\n"
        await update.message.reply_text(response_strings)
    
# Function to handle text messages
async def handle_text(update: Update, context: CallbackContext):
    user_text = update.message.text
    if await allowed_users_only(update):
        chatgpt_response = get_chatgpt_response(user_text, context.user_data["chat context"])
        logger.info("latest context is: " + str(context.user_data["chat context"]))
        await update.message.reply_text(text=chatgpt_response)

# Function to get a response from ChatGPT-3.5
def get_chatgpt_response(prompt, chat_context):
    chat_context.append({"role": "user", "content": prompt})
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0301",
        messages=chat_context
    )
    response_text = response.choices[0].message.content.strip()
    chat_context.append({"role": "assistant", "content": response_text})
    return response_text

app = Chalice(app_name=APP_NAME)
application = Application.builder().token(TELEGRAM_API_TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('clear', clear))
application.add_handler(CommandHandler('add_user', add_user))
application.add_handler(CommandHandler('users', users))
application.add_handler(CommandHandler('delete_user', delete_user))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
print("Registered all the handlers")

@app.lambda_function(name=LAMBDA_MESSAGE_HANDLER)
def message_handler(event, context):
    print("Try to initialise")
    async_to_sync(application.initialize, force_new_loop=True)()
    print("Got something from Telegram: " + str(event))
    print("And some context: " + str(context))
    try:
        print("Trying to process the update")
        if "message" in event["body"]:
            print("Preparing the update")
            async_to_sync(application.start)()
            update = Update.de_json(data=json.loads(event["body"]), bot=application.bot)
            async_to_sync(application.update_queue.put)(update)
            print("Let's process the update: " + str(update))
            async_to_sync(application.process_update)(update)
            async_to_sync(application.stop)()
            print("Update processed")
        else:
            print("Not a message event")
    except Exception as e:
        logger.warning(e)
        print("Exception happened: " + e)
        # async_to_sync(application.stop)()
        return {"statusCode": 500}
    # async_to_sync(application.stop)()
    return {"statusCode": 200}


# if __name__ == '__main__':
#     #main()
#     message_handler({'body':'{"update_id":"1"}'},{})
    
