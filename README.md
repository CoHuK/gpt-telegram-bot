# Telegram Bot with Chalice and AWS Lambda

GPT Telegram Bot using AWS features
- Lambda, DynamoDB (full auto deployment)
- Fargate, ECS (manual Docker container deployment)

## Requirements

- Python 3.9+
- AWS CLI
- AWS credentials and region setup in the \`~/.aws/credentials\` file
- Chalice

## Deployment to AWS Lambda

1. Create a Free Tier AWS account

2. Install and setup AWS CLI (region must be specified in ~/.aws/credentials. Ex.: region=eu-west-1)

3. Update the \`.chalice/config.json\` file with your Telegram Bot API, openAI API Token, your telegram userID (get it from @userinfobot).

4. Run the `deploy.sh` script to deploy the Telegram Bot to AWS Lambda (or `deploy.sh name` for a custom config)

## Features

- Currently used openAI model: gpt-3.5-turbo-0301
- Context of your chat is saved until you use command `/clear`

**Warning**
> :warning: Make sure to clean the context often, as the full context is sent with every message, so longer you talk about one topic to GPT, more expensive become the processing of the response.

- Multi-config support: keep multiple chalice configs to deploy multiple bots `.chalice/name.config.json`