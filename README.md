# alpaca-trading-bot
Trading bot written in Python to trigger trades in Alpaca

# What this bot does?
This trading bot takes the message sent from TradingView (charting service) and execute the trades based on rule based algorithm in Alpaca. I have used AWS Chalice, Lambda, Cloudwatch, S3 and Python for this bot.

# What is AWS Chalice?
Chalice is a framework for writing serverless apps in python. I have used Chalice mainly to deploy API end point in AWS. This end point is required to send message from TradingView via REST API. You can use the same approach to send API calls to AWS even if you don't use TradingView. For more details about Chalice, check out https://github.com/aws/chalice. 

# Architecture
https://user-images.githubusercontent.com/46457863/115506031-022a7e00-a298-11eb-9fc7-751858cd9fc2.png
