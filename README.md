# alpaca-trading-bot
Trading bot written in Python to trigger trades in Alpaca

# What this bot does?
This trading bot takes the message sent from TradingView (charting service) and execute the trades based on rule based algorithm in Alpaca. I have used AWS Chalice, Lambda, Cloudwatch, S3 and Python for this bot.

# What is AWS Chalice?
Chalice is a framework for writing serverless apps in python. I have used Chalice mainly to deploy API end point in AWS. This end point is required to send message from TradingView via REST API. You can use the same approach to send API calls to AWS even if you don't use TradingView. For more details about Chalice, check out https://github.com/aws/chalice. 

# Architecture
![Trading bot architecture](https://user-images.githubusercontent.com/46457863/115507483-d3150c00-a299-11eb-84fc-b47550c1ca32.png)
