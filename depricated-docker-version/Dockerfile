FROM --platform=linux/amd64 python:3.8-slim-buster

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r docker_requirements.txt

COPY gpt-telegram-bot.py .

EXPOSE 80

CMD ["python", "gpt-telegram-bot.py"]
