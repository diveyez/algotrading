"""Risk Management module."""

import time
from typing import Tuple
import cryptoalgotrading.lib_bittrex as lib_bittrex
from binance.client import Client as Bnb
import cryptoalgotrading.var as var
import cryptoalgotrading.aux as aux
import logging as log


class Bittrex:

    def __init__(self, key, secret):
        # connects to bittrex through Bittrex lib.
        self.conn = lib_bittrex.Bittrex(key, secret)
        # min_limit represents minimum value account needs, in order to remain working.
        self.min_limit = var.usdt_min
        # risk represents percentage of money bot could use each time it buy coins.
        self.risk = var.risk
        # available balances.
        self.available = {
            i["Currency"]: i["Available"]
            for i in self.conn.get_balances()["result"]
            if i["Available"] > 0
        }

    def get_all_balances(self):
        # parser do get_balances
        return self.conn.get_balances()

    def get_coin_balance(self, coin):
        return self.conn.get_balance(coin)["result"]["Available"], \
               self.conn.get_balance(coin)["result"]["Pending"]

    def buy(self, coin, amount, price):
        """
        Method to buy coins.
        :param coin: coin to buy
        :param amount: amount of currency to buy
        :param price: limit price
        :return: tuple with info about the purchase
        """
        # Verify if has sufficient funds.
        if self.available[coin.split('-')[0]] <= self.min_limit:
            # Insufficient funds.
            return False, "Cash under Minimum limit."
        # Calculate the amount of 'coin' to buy, based on rate and risk.
        to_spend = self.available[coin.split('-')[0]] * self.risk

        # Buy_limit
        res = self.conn.buy_limit(coin, rate/to_spend, rate)

        if not res["success"]:
            # Buy_limit didn't went as predicted.
            return False, res["message"]

        # REMOVE SLEEP
        time.sleep(1)
        order = self.conn.get_order(res["result"]["uuid"])

        if not order["result"]["IsOpen"]:
            # Returns True and price payed for coin.
            return True, [order["result"]["PricePerUnit"], order["result"]["Quantity"]]

        cancel = self.conn.cancel(res["result"]["uuid"])
        # Couldn't buy at desired rate.
        return False, cancel["message"]

    def sell(self, coin, quantity, rate):

        self.conn.sell_limit(coin, quantity, rate)
        return True


class Binance:

    def __init__(self):
        # connects to Binance through Binance lib.
        self.conn = aux.Binance(var.bnc_ky, var.bnc_sct)
        # min_limit represents minimum value account needs, in order to remain working.
        self.min_limit = {'USDT': var.usdt_min,
                          'BTC': var.btc_min}
        # Binance has limitations in float precision.
        self.coin_precision = {'USDT': 2,
                               'BTC': 8}
        # risk represents percentage of money bot could use each time it buy coins.
        self.risk = var.risk
        # available balances.
        self.assets = {}

        self.init_balance()

    def init_balance(self):
        for coin in self.conn.get_account()["balances"]:
            self.assets[coin['asset']] = {'available': float(coin['free']),
                                          'pending': float(coin['locked']),
                                          'info': {}}

    def refresh_balance(self) -> None:
        """Update balance for all pairs."""

        for coin in self.conn.get_account()["balances"]:
            try:
                self.assets[coin['asset']]['available'] = float(coin['free'])
                self.assets[coin['asset']]['pending'] = float(coin['locked'])
            except Exception:
                self.assets[coin['asset']] = {'available': float(coin['free']),
                                              'pending': float(coin['locked']),
                                              'info': {}}
                log.debug(f"[ADD] New coin - {coin['asset']}")

    def get_balances(self, coins=None) -> dict:
        """
        Get the current balance for one or multiple assets.
        :param coins: None for all assets, string for one asset and a list of strings for multiple assets
        :return: dict with assets.
        """
        self.refresh_balance()

        if not coins:
            return self.assets
        elif isinstance(coins, list):
            return {coin: self.assets[coin] for coin in coins}
        else:
            return self.assets[coins]

    def buy(self,
            coin: str,
            currency: str = 'USDT',
            # amount: float = 0,
            price: float = 0) -> Tuple[bool, dict]:
        """
        Buy method to use in real mode operation.
        :param currency:
        :param coin: pair to buy
        :param amount: quantity of asset to buy
        :param price: price to buy
        :return: tuple with bool representing the success of the operation
        and a dict with the transaction info
        """
        # Verify if has sufficient funds.
        self.refresh_balance()

        cur_balance = self.assets[currency]['available'] + \
                      self.assets[currency]['pending']

        if cur_balance < self.min_limit[currency]:
            return False, {'error': 'Insufficient funds'}

        # Calculate quantity to buy based on preset risk
        quantity_to_buy = cur_balance * self.risk

        if quantity_to_buy > self.assets[currency]['available']:
            return False, {'error': 'Portfolio has no space for new assets'}

        try:
            if not price:
                buy_order = self.conn.order_market_buy(symbol=coin,
                                                       quoteOrderQty=round(quantity_to_buy,
                                                                           self.coin_precision[currency])
                                                       )
            else:
                buy_order = self.conn.order_limit_buy(symbol=coin,
                                                      price=price,
                                                      quantity=round(quantity_to_buy/price,
                                                                     self.coin_precision[currency]))
        except Exception as e:
            return False, {'error': e}

        # Tests buy
        if buy_order['status'] == 'FILLED':
            self.assets[coin.replace(currency, '')]['info'] = self.asset_info(coin)
            return True, buy_order
        # else:
        #    self.cancel_order(buy_order['number'])
        return False, buy_order

    def sell(self,
             coin: str,
             currency: str = 'USDT',
             quantity: float = 0) -> Tuple[bool, dict]:
        """
        Market sell assets.
        :param coin: pair to sell
        :param currency: quote coin to exchange with
        :param quantity: quantity of coin to sell
        :return: Tuple with operation success bool and dict with more info
        """
        # balance = client.get_asset_balance(asset=best_match[0])
        self.refresh_balance()
        # Market Sell
        if not quantity:
            # Quantity available to sell during the precision constrains.
            try:
                prec_quantity = round(self.assets[coin.replace(currency, '')]['available'] - \
                                (self.assets[coin.replace(currency, '')]['available'] %
                                 self.assets[coin.replace(currency, '')]['info']['lot_size']),
                                 self.assets[coin.replace(currency, '')]['info']['precision'])
                #if coin.replace(currency, '') == 'BNB':
                #    prec_quantity = prec_quantity
                sell_order = self.conn.order_market_sell(symbol=coin,
                                                         quantity=str(prec_quantity))
            except Exception as e:
                return False, {'error': e,
                               'coin': coin,
                               'prec': prec_quantity,
                               'available': self.assets[coin.replace(currency, '')]['available'],
                               'more info': self.assets[coin.replace(currency, '')]['info']
                               }

        return True, sell_order

    # TODO - cancel_order
    def cancel_order(self,
                     order_number): 
        return True

    def asset_info(self, symbol) -> dict:
        """
        Gets specific info for buy and sell orders.
        :param symbol: symbol
        :return: dictionary with info about symbol
        """
        d = self.conn.get_symbol_info(symbol)

        return {'symbol': d['symbol'],
                'precision': d['quoteAssetPrecision'],
                'lot_size': float([a['stepSize'] for a in d['filters'] if a['filterType'] == 'LOT_SIZE'][0]),
                'more': d}

    def get_ticker(self):
        return self.conn.get_ticker()

    def sell_all(self):
        """Sell all avalilable assets."""

        self.refresh_balance()

        for coin in self.assets():
            if self.assets[coin]['available'] > 0:
                self.sell(coin)
        return True

    def cancel_order(self,
                     symbol: str,
                     order_id: str) -> dict:
        """Cancels an pending order."""

        return self.conn.cancel_order(symbol=symbol, orderId=order_id)
