import os
import openai
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, PicklePersistence

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load the API tokens from the .env file
load_dotenv()
TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Initialize the OpenAI library
openai.api_key = OPENAI_API_KEY

# Function to start the bot
async def start(update: Update, context: CallbackContext):
    context.user_data["chat context"] = []
    await update.message.reply_text('Welcome to the ChatGPT-3.5 bot! Type your message and I will respond.')

async def clear(update: Update, context: CallbackContext):
    context.user_data["chat context"] = []
    logger.warning("Context should be cleared by now: " + str(context.user_data["chat context"]))
    
    
# Function to handle text messages
async def handle_text(update: Update, context: CallbackContext):
    user_text = update.message.text
    chatgpt_response = get_chatgpt_response(user_text, context.user_data["chat context"])
    logger.warning("latest context is: " + str(context.user_data["chat context"]))
    await update.message.reply_text(chatgpt_response)

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

def main():
    application = Application.builder().token(TELEGRAM_API_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('clear', clear))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()
