'''
############ TRADING BOT #############
This bot gets the signal via webhook from TradingView and executes the trades in Alpaca
'''

import alpaca_trade_api as tradeapi
import requests, json
import time, re
import os
import pandas as pd
import datetime as dt
from datetime import datetime
from chalice import Chalice

app = Chalice(app_name='alpaca_ib_atr')

# Config
total_buying_power = int(200000)
number_positions_per_day = int(5)
dollar_available_per_trade = total_buying_power / number_positions_per_day

API_KEY = '<Alpaca API Key goes here>'
SECRET_KEY = '<Alpaca Secret key goes here>'
BASE_URL = "https://paper-api.alpaca.markets" # for paper trading
ORDERS_URL = "{}/v2/orders".format(BASE_URL)
HEADERS = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY}

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, 'v2')

@app.route('/alpaca_trade', content_types=['text/plain'], methods=['POST'])
def alpaca_trade():

    request = app.current_request
    webhook_message = request.raw_body.decode() # need to decode because of plain text
    x = webhook_message.split(";")
    d = {}
    for val in x:
        val1 = val.split('=')
        d[val1[0]] = val1[1]

    symbol = d['ticker']
    atr = round_nearest(float(d['atr']), 0.05)
    high = round_nearest(float(d['high']), 0.05)
    low = round_nearest(float(d['low']), 0.05)
    close = round_nearest(float(d['close']), 0.05)
    ignore_atr = d['ignore_atr']
    indicator = d['indicator']
    incoming_alert_hour = int(d['hour'])
    incoming_alert_minute = int(d['minute'])
    print("Incoming data: Symbol: " + symbol + "; Indicator: " + indicator + "; Close: " + str(close) + "; Low: " + str(low) + "; atr: " + str(atr))

    # Get variables for MAX_TP_ATR, etc
    get_env_variables()

    ### Validate multiple conditions before initiating Trade

    if(incoming_alert_hour >= TRADE_CUTOFF_HOUR):
        error_message = "Trading is not allowed after " + str(TRADE_CUTOFF_HOUR)
        print(error_message)
        return error_message

    ## Check if there is existing open position for the same ticker. If yes, dont proceed
    ## Trying to avoid buying lot of shares in the same stock
    try:
        position = int(api.get_position(symbol).qty)
    except:
        # No position exists
        position = 0

    if position != 0:
        error_message = "Alpaca has existing positions in " + symbol
        print(error_message)
        return error_message

    '''
    I want to avoid losing money on just profitable trade. Keep profits!
    If the last sell side trade happened in the last 30 minutes (configurable via TRADING_GAP_MINUTES), don't buy the same stock
    '''
    closed_orders = api.list_orders(status='closed', limit=100, direction='desc')
    closed_symbol_orders = [o for o in closed_orders if o.symbol == symbol]

    system_dt = datetime.utcnow() # filled_at is stored as UTC timestamp
    timedelta_minutes = 999

    for i in closed_symbol_orders:
        if i.side == 'sell' and i.status == 'filled':
            if i.order_type == 'limit' or i.order_type == 'stop':
                filled_at = i.filled_at
                timedelta = system_dt - filled_at.to_pydatetime().replace(tzinfo=None)
                timedelta_minutes = timedelta.total_seconds()/60
                break

    if timedelta_minutes < TRADING_GAP_MINUTES:
        error_message = "Wait for sometime before placing the order on this stock; Timedelta is: " + str(timedelta_minutes) + " Last filled at: " + str(filled_at)
        print(error_message)
        return error_message

    # Check if there is at least $200 profit today. If yes, take the profit and run!
    latest_profit_loss = get_latest_profit_loss(api, symbol)
    if latest_profit_loss >= TAKE_THIS_PROFIT_AND_RUN:
        error_message = "You made $" + str(latest_profit_loss) + " in " + symbol + " today. Dont push your luck!"
        print(error_message)
        return error_message

    print("Latest profit/loss for " + symbol + " is: " + str(latest_profit_loss))

    #Is there any open orders, probably bracket orders not filled yet
    open_orders = api.list_orders(status='open')
    open_symbol_orders = [o for o in open_orders if o.symbol == symbol]
    if len(open_symbol_orders) != 0:
        error_message = "Open order exists for this security"
        print(error_message)
        return error_message

    #### VALIDATION ENDS

    ## Get current price to calculate approx quantity to buy
    symbol_bars = api.get_barset(symbol, 'minute', 1).df.iloc[0]
    symbol_price = symbol_bars[symbol]['close']

    # Determine quantity
    quantity_of_shares = round(dollar_available_per_trade / symbol_price)

    #Don't buy more than 1000 shares in one transaction
    if quantity_of_shares > 1000:
        quantity_of_shares = 1000

    if indicator == "Experiment":
        quantity_of_shares = 1 # limit quantity for experiments

    if indicator == "execute-bracket-order":
        bracket_order_id = execute_bracket_order(symbol, quantity_of_shares, close, low, atr, ignore_atr, indicator)
        return bracket_order_id

    buy_order_data = api.submit_order(
        symbol=symbol,
        qty=quantity_of_shares,
        side='buy',
        type='market',
        time_in_force='day'
    )

    #Wait for one second to make sure the order is filled
    time.sleep(2)

    my_buy_order = api.get_order_by_client_order_id(buy_order_data.client_order_id)
    filled_price = my_buy_order.filled_avg_price

    if filled_price is None:
        #Log the data
        purchase_order_id = my_buy_order.client_order_id
        sale_order_id = 'filled-price-not-returned'
        purchase_filled_at = 0
        purchase_qty = quantity_of_shares
        filled_price = 0
        tp_atr = 0
        sl_atr = 0
        trade_date = datetime.today().strftime('%Y-%m-%d')
        log_data(trade_date, symbol, indicator, purchase_order_id, filled_price, purchase_filled_at, purchase_qty, tp_atr, sl_atr, sale_order_id)

        #Print error message
        error_message = "Alpaca did not return filled price"
        print(error_message)

        return error_message

    print("Alpaca filled at " + str(filled_price))

    # Calculate profit taking and stop loss
    if ignore_atr == "True": ## if the signal is not strong, keep target small
        tp_atr = MIN_ATR_DESIRED
        sl_atr = 2
        target_profit = float(filled_price) + tp_atr
        stop_loss = float(filled_price) - sl_atr
    else:
        if atr > MAX_TP_ATR:
            tp_atr = MAX_TP_ATR
            sl_atr = atr
        else:
            tp_atr = atr
            sl_atr = atr
        target_profit = float(filled_price) + tp_atr
        stop_loss = low - sl_atr
        if stop_loss >= float(filled_price): # Check for edge case where the price got filled lower than "low" sent by tradingview
            stop_loss = float(filled_price) - sl_atr

    #Experiment to test trailing stop
    if atr > 10 and ignore_atr == "False":
        trail_price = atr + 3 # 3 is random
        sell_trailing_stop = api.submit_order(
            symbol=symbol,
            qty=quantity_of_shares,
            side='sell',
            type='trailing_stop',
            time_in_force='day',
            trail_price=trail_price
        )
        my_sell_order = api.get_order_by_client_order_id(sell_trailing_stop.client_order_id)
    else:
        sell_order_data = api.submit_order(
            symbol=symbol,
            qty=quantity_of_shares,
            side='sell',
            type='limit',
            time_in_force='day',
            order_class='oco',
            stop_loss={'stop_price': stop_loss},
            take_profit={'limit_price': target_profit}
        )
        my_sell_order = api.get_order_by_client_order_id(sell_order_data.client_order_id)

    #Log the data in S3 for later analysis
    purchase_order_id = my_buy_order.client_order_id
    sale_order_id = my_sell_order.client_order_id
    purchase_filled_at = my_buy_order.filled_at
    purchase_qty = my_buy_order.filled_qty
    trade_date = datetime.today().strftime('%Y-%m-%d')
    log_data(trade_date, symbol, indicator, purchase_order_id, filled_price, purchase_filled_at, purchase_qty, tp_atr, sl_atr, sale_order_id)
    print("logging is complete")

    return filled_price

def execute_bracket_order(symbol, quantity_of_shares, close, low, atr, ignore_atr, indicator):

    limit_price = close - 0.50

    if atr > MAX_TP_ATR:
        tp_atr = MAX_TP_ATR
        sl_atr = atr
    else:
        tp_atr = atr
        sl_atr = atr
    target_profit = limit_price + tp_atr
    stop_loss = low - sl_atr - 0.50

    bracket_order_data = api.submit_order(
        symbol=symbol,
        qty=quantity_of_shares,
        side='buy',
        type='limit',
        limit_price=limit_price,
        order_class='bracket',
        take_profit={'limit_price':target_profit},
        stop_loss={'stop_price':stop_loss},
        time_in_force='day'
    )

    bracket_order_data_order_id = bracket_order_data.client_order_id

    #Log the order in S3
    purchase_order_id = bracket_order_data_order_id
    purchase_qty = quantity_of_shares
    filled_price = 0
    purchase_filled_at = 0
    sale_order_id = 'bracket order'
    trade_date = datetime.today().strftime('%Y-%m-%d')
    log_data(trade_date, symbol, indicator, purchase_order_id, filled_price, purchase_filled_at, purchase_qty, tp_atr, sl_atr, sale_order_id)
    print("bracket order logging is complete")

    return purchase_order_id

def get_env_variables():
    global MAX_TP_ATR, MIN_ATR_DESIRED, TRADING_GAP_MINUTES, MAX_OPEN_POSITIONS, TRADE_CUTOFF_HOUR, TAKE_THIS_PROFIT_AND_RUN
    MAX_TP_ATR = int(os.environ['MAX_TP_ATR'])
    MIN_ATR_DESIRED = float(os.environ['MIN_ATR_DESIRED'])
    TRADING_GAP_MINUTES = int(os.environ['TRADING_GAP_MINUTES'])
    MAX_OPEN_POSITIONS = int(os.environ['MAX_OPEN_POSITIONS'])
    TRADE_CUTOFF_HOUR = int(os.environ['TRADE_CUTOFF_HOUR'])
    TAKE_THIS_PROFIT_AND_RUN = float(os.environ['TAKE_THIS_PROFIT_AND_RUN'])

def round_nearest(x, a):
    return round(round(x / a) * a, 2)

def log_data(date, symbol, indicator, purchase_order_id, purchase_price, purchase_filled_at, quantity, tp_atr, sl_atr, sale_order_id):
    import boto3
    from io import StringIO
    import datetime as dt
    print("in log data")

    # get your credentials from environment variables
    aws_id = '<aws_id>'
    aws_secret = '<aws_secret>'
    client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

    bucket_name = 'trading-bot-alpaca'
    object_key = 'alpaca_paper_trade.csv'

    if purchase_filled_at != 0:
        purchase_filled_at = pd.to_datetime(purchase_filled_at, format="%Y-%m-%d %H:%M:%S")
        purchase_filled_at = purchase_filled_at.tz_convert('America/New_York')
        purchase_filled_at = purchase_filled_at.strftime("%Y-%m-%d %H:%M:%S")

    data_to_add = {'date': date, 'symbol': symbol, 'indicator': indicator, 'purchase_order_id': purchase_order_id, 'purchase_price': purchase_price, 'purchase_filled_at': purchase_filled_at, 'quantity': quantity, 'tp_atr': tp_atr, 'sl_atr': sl_atr, 'sale_order_id': sale_order_id}
    df = pd.DataFrame([data_to_add])
    bytes_to_write = df.to_csv(None, header=False, index=False)

    current_data_obj = client.get_object(Bucket=bucket_name, Key=object_key)
    body = current_data_obj['Body']
    current_data = body.read().decode('utf-8')

    appended_data = current_data + bytes_to_write
    appended_data_encoded = appended_data.encode()
    client.put_object(Body=appended_data_encoded, Bucket=bucket_name, Key=object_key)

def get_latest_profit_loss(api, symbol):
    '''
    Get all closed orders and import them into a dataframe
    :param api: alpaca trade api
    :param symbol: incoming symbol
    :return: profit/loss
    '''
    orderTotal = 100
    today = dt.date.today() - dt.timedelta(days=0)

    closed_orders = api.list_orders(status='closed', limit=orderTotal, after=today, direction='desc')
    orders = [o for o in closed_orders if o.symbol == symbol]
    if (len(orders) == 0):
      return 0

    dfOrders = pd.DataFrame()
    for o in orders:
      d = vars(o)
      # import dict into dataframe
      df = pd.DataFrame.from_dict(d, orient='index')
      # append to dataframe
      dfOrders = dfOrders.append(df, ignore_index=True)

    # select filled orders with buy or sell
    dfSel = dfOrders
    # choose a subset (use .copy() as we are slicing and to avoid warning)
    dfSel = dfSel[['client_order_id', 'submitted_at', 'filled_at', 'symbol', 'filled_qty', 'side', 'type', 'filled_avg_price', 'status']].copy()

    # convert filled_at to date
    dfSel['submitted_at'] = pd.to_datetime(dfSel['submitted_at'], format="%Y-%m-%d %H:%M:%S")
    dfSel['filled_at']    = pd.to_datetime(dfSel['filled_at'], format="%Y-%m-%d %H:%M:%S")
    # convert to our timezone
    dfSel['submitted_at'] = dfSel['submitted_at'].dt.tz_convert('America/New_York')
    dfSel['filled_at']    = dfSel['filled_at'].dt.tz_convert('America/New_York')
    # remove millis
    dfSel['submitted_at'] = dfSel['submitted_at'].dt.strftime("%Y-%m-%d %H:%M:%S")
    dfSel['filled_at']    = dfSel['filled_at'].dt.strftime("%Y-%m-%d %H:%M:%S")

    dfSel['type'] = pd.Categorical(dfSel['type'], categories=["market", "limit", "stop_limit", "stop", "trailing_stop"])
    # sort first based on symbol, then type as per the list above, then submitted date
    dfSel.sort_values(by=['symbol', 'submitted_at', 'type'], inplace=True, ascending=False)

    dfSel.reset_index(drop=True, inplace=True)
    dfProfit = dfSel
    dfProfit['profit'] = ''

    totalProfit = 0.0
    profitCnt   = 0
    lossCnt     = 0
    slCnt       = 0
    ptCnt       = 0
    trCnt       = 0
    qty         = 0
    profit      = 0
    sign        = {'buy': -1, 'sell': 1}

    for index, row in dfSel.iterrows():
      if index > 0:
        if dfSel['symbol'][index - 1] != dfSel['symbol'][index]:
          qty    = 0
          profit = 0

      if dfSel['status'][index] == 'held':
        continue
      if dfSel['status'][index] == 'new':
        continue
      if dfSel['filled_avg_price'][index] is None:
        continue
      if dfSel['filled_avg_price'][index] == '':
        continue
      if dfSel['filled_avg_price'][index] == 'None':
        continue

      side      = dfSel['side'][index]
      filledQty = int(dfSel['filled_qty'][index]) * sign[side]
      qty       = qty + filledQty
      price     = float(dfSel['filled_avg_price'][index])
      pl        = filledQty * price
      profit    = profit + pl

      if qty==0:
        # complete trade
        trCnt = trCnt + 1
        # put the profit in its column
        dfProfit.loc[index, 'profit'] = round(profit, 2)
        totalProfit = totalProfit + profit
        if profit >= 0:
          profitCnt = profitCnt + 1
          if dfSel['type'][index] == 'limit':
            ptCnt = ptCnt + 1
        else:
            lossCnt = lossCnt + 1
            if dfSel['type'][index] == 'stop_limit' or dfSel['type'][index] == 'stop' or dfSel['type'][index] == 'trailing_stop':
              slCnt = slCnt + 1
        profit = 0

    for i, row in dfProfit.iterrows():
        #Return the first available latest profit/loss
        if(isinstance(row['profit'], float)):
            return(row['profit'])

    return 0