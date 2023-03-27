#!/bin/bash
set -e

check_requirements() {
  command -v aws >/dev/null 2>&1 || { echo >&2 "AWS CLI required. Install with 'pip install awscli'. Aborting."; exit 1; }
  command -v chalice >/dev/null 2>&1 || { echo >&2 "Chalice required. Install with 'pip install chalice'. Aborting."; exit 1; }
  command -v jq >/dev/null 2>&1 || { echo >&2 "jq required. Install with 'apt install jq' or 'brew install jq'. Aborting."; exit 1; }
  command -v curl >/dev/null 2>&1 || { echo >&2 "curl required. Install with 'apt install curl' or 'brew install curl'. Aborting."; exit 1; }
}

check_requirements
# Check if AWS CLI has valid credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "Error: AWS CLI has no valid credentials. Please configure it and try again."
    exit 1
fi

# Check if the script received a config name argument
if [ $# -eq 0 ]; then
  echo "Using the default config: .chalice/config.json"
else
  CONFIG_NAME=$1
  echo "Using config .chalice/${CONFIG_NAME}.config.json"
  # Check if the specified config file exists
  if [ ! -f ".chalice/${CONFIG_NAME}.config.json" ]; then
    echo "Config file .chalice/${CONFIG_NAME}.config.json not found."
    exit 1
  else
    # Copy the specified config file to .chalice/config.json
    mv .chalice/config.json .chalice/backup.config.json >/dev/null 2>&1 || true 
    cp -f ".chalice/${CONFIG_NAME}.config.json" .chalice/config.json
  fi
fi


# Get DynamoDB table names from the config file
DYNAMODB_TABLE_NAME=$(jq -r '.environment_variables.DYNAMODB_TABLE_NAME' .chalice/config.json)
DYNAMODB_USERS_TABLE_NAME=$(jq -r '.environment_variables.DYNAMODB_USERS_TABLE_NAME' .chalice/config.json)

# Check and create DynamoDB tables if they don't exist
for table_name in "$DYNAMODB_TABLE_NAME" "$DYNAMODB_USERS_TABLE_NAME"; do
  if ! aws dynamodb describe-table --table-name "$table_name" >/dev/null 2>&1; then
    echo "Creating table $table_name"
    if [ "$table_name" == "$DYNAMODB_TABLE_NAME" ]; then
      aws dynamodb create-table \
        --table-name "$table_name" \
        --attribute-definitions \
          AttributeName=user_id,AttributeType=S \
          AttributeName=message_id,AttributeType=N \
        --key-schema \
          AttributeName=user_id,KeyType=HASH \
          AttributeName=message_id,KeyType=RANGE \
        --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1
    elif [ "$table_name" == "$DYNAMODB_USERS_TABLE_NAME" ]; then
      aws dynamodb create-table \
        --table-name "$table_name" \
        --attribute-definitions \
          AttributeName=user_id,AttributeType=S \
          AttributeName=user_type,AttributeType=S \
        --key-schema \
          AttributeName=user_id,KeyType=HASH \
          AttributeName=user_type,KeyType=RANGE \
        --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1
    fi
  else
    echo "DynamoDB table $table_name found!"
  fi
done

echo "Deploy the chalice app"
chalice deploy

APP_NAME=$(jq -r '.app_name' ".chalice/config.json")
# Get the Lambda function name
LAMBDA_ARN=$(aws lambda list-functions | jq -r ".Functions[] | select(.FunctionName | startswith(\"${APP_NAME}\")) | .FunctionName")

if [ -z "$LAMBDA_ARN" ]; then
  echo "Lambda function ARN not found. Exiting."
  exit 1
fi
echo "Function name: $LAMBDA_ARN"
echo "---"

# Create or get the function URL config
if aws lambda get-function-url-config --function-name "$LAMBDA_ARN" > /dev/null 2>&1; then
  FUNCTION_URL=$(aws lambda get-function-url-config --function-name "$LAMBDA_ARN" | jq -r '.FunctionUrl')
else
  FUNCTION_URL=$(aws lambda create-function-url-config --function-name "$LAMBDA_ARN" --auth-type NONE | jq -r '.FunctionUrl')
fi
if [ -z "$FUNCTION_URL" ]; then
  echo "Couldn't get Lambda Function URL. Exiting."
  exit 1
fi
echo "Lambda Function URL: $FUNCTION_URL"

echo "Add lambda:InvokeFunctionUrl permissions for Lambda function"
aws lambda add-permission \
  --function-name $LAMBDA_ARN \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type "NONE" \
  --statement-id url >/dev/null 2>&1 | echo "Permission lambda:InvokeFunctionUrl already added"

# Attach policy to the existing role
stage="dev"
role_name="${APP_NAME}-${stage}"
policy_arn="arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
# Inform the user about checking the policy
echo "Checking if AmazonDynamoDBFullAccess policy is attached to the role..."

# Check if the policy is already attached to the role
is_policy_attached=$(aws iam list-attached-role-policies --role-name "$role_name" | jq -r ".AttachedPolicies[] | select(.PolicyArn == \"$policy_arn\") | .PolicyArn")

# Attach the policy if it's not attached
if [ -z "$is_policy_attached" ]; then
  echo "Attaching AmazonDynamoDBFullAccess policy to the role..."
  aws iam attach-role-policy --role-name "$role_name" --policy-arn "$policy_arn"
else
  echo "AmazonDynamoDBFullAccess policy is already attached to the role."
fi

# Extract the TELEGRAM_API_TOKEN from config.json
TELEGRAM_API_TOKEN=$(jq -r '.environment_variables.TELEGRAM_API_TOKEN' .chalice/config.json)

echo "Set the Telegram Bot webhook"
echo "url=${FUNCTION_URL}."
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_API_TOKEN}/setWebhook" -d "url="
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_API_TOKEN}/setWebhook" -d "url=${FUNCTION_URL}"
