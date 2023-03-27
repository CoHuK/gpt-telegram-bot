# GPT Telegram Bot with easy deployment using Chalice to AWS Lambda
<p align="left">
<img
  src="https://user-images.githubusercontent.com/1978717/227817754-219a8e0d-8a79-4cbb-8d1e-e348887bfa73.jpg"
  alt="Bot Demo"
  title="Happy Programmer"
  style="display: inline-block; margin: 0 auto; max-width: 150px; width: 324px; height: 657px">
</p>

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

For voice processing you will need manually (will be automated in the next release):

1. Upload ffmpeg.zip to your S3 bucket
2. Create Lambda Layer using S3 URI
3. Attach layer to your Lambda function

## Features

- Currently used openAI model: gpt-3.5-turbo-0301 (Just type any message)
- Image generation `/image prompt`
- Voice message transcript (Just send any voice message)
- Context of your chat is saved until you use command `/clear`
- Price in USD of the response is shown

> :warning: Make sure to clean the context often, as the full context is sent with every message, so longer you talk about one topic to GPT, more expensive become the processing of the response.

- Multi-config support: keep multiple chalice configs to deploy multiple bots `.chalice/name.config.json`
