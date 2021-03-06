# encoding: UTF-8

"""
本模块中主要包含：
1. 从通联数据下载历史行情的引擎
2. 用来把MultiCharts导出的历史数据载入到MongoDB中用的函数
"""

from datetime import datetime, timedelta
import pymongo
from time import time
from multiprocessing.pool import ThreadPool

from ctaBase import *
from vtConstant import *
from vtFunction import loadMongoSetting
from datayesClient import DatayesClient

import json


# 以下为vn.trader和通联数据规定的交易所代码映射 
VT_TO_DATAYES_EXCHANGE = {}
VT_TO_DATAYES_EXCHANGE[EXCHANGE_CFFEX] = 'CCFX'     # 中金所
VT_TO_DATAYES_EXCHANGE[EXCHANGE_SHFE] = 'XSGE'      # 上期所 
VT_TO_DATAYES_EXCHANGE[EXCHANGE_CZCE] = 'XZCE'       # 郑商所
VT_TO_DATAYES_EXCHANGE[EXCHANGE_DCE] = 'XDCE'       # 大商所
DATAYES_TO_VT_EXCHANGE = {v:k for k,v in VT_TO_DATAYES_EXCHANGE.items()}


########################################################################
class HistoryDataEngine(object):
    """CTA模块用的历史数据引擎"""

    # ----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        host, port = loadMongoSetting()

        self.dbClient = pymongo.MongoClient(host, port)
        self.datayesClient = DatayesClient()

    # ----------------------------------------------------------------------
    def lastTradeDate(self):
        """获取最近交易日（只考虑工作日，无法检查国内假期）"""
        today = datetime.now()
        oneday = timedelta(1)

        if today.weekday() == 5:
            today = today - oneday
        elif today.weekday() == 6:
            today = today - oneday * 2

        return today.strftime('%Y%m%d')

    # ----------------------------------------------------------------------
    def readFuturesProductSymbol(self):
        """查询所有期货产品代码"""
        cx = self.dbClient[SETTING_DB_NAME]['FuturesSymbol'].find()
        return set([d['productSymbol'] for d in cx])  # 这里返回的是集合（因为会重复）

    # ----------------------------------------------------------------------
    def readFuturesSymbol(self):
        """查询所有期货合约代码"""
        cx = self.dbClient[SETTING_DB_NAME]['FuturesSymbol'].find()
        return [d['symbol'] for d in cx]  # 这里返回的是列表

    # ----------------------------------------------------------------------
    def downloadFuturesSymbol(self, tradeDate=''):
        """下载所有期货的代码"""
        if not tradeDate:
            tradeDate = self.lastTradeDate()

        self.dbClient[SETTING_DB_NAME]['FuturesSymbol'].ensure_index([('symbol', pymongo.ASCENDING)],
                                                                     unique=True)

        path = 'api/market/getMktMFutd.json'

        params = {}
        params['tradeDate'] = tradeDate

        data = self.datayesClient.downloadData(path, params)

        if data:
            for d in data:
                symbolDict = {}
                symbolDict['symbol'] = d['ticker']
                symbolDict['productSymbol'] = d['contractObject']
                flt = {'symbol': d['ticker']}

                self.dbClient[SETTING_DB_NAME]['FuturesSymbol'].update_one(flt, {'$set': symbolDict},
                                                                           upsert=True)
            print u'期货合约代码下载完成'
        else:
            print u'期货合约代码下载失败'

    # ----------------------------------------------------------------------
    def downloadFuturesBar(self, symbol, unit):
        """
        下载期货合约的日、周、月行情，symbol是合约代码，unit包括daily、weekly、monthly，
        """
        print u'开始下载%s %s行情' % (symbol, unit)

        # unit包括daily、weekly、monthly，根据unit值对DB_NAME和path进行赋值
        DB_NAME = ""
        if unit == "daily":
            DB_NAME = DAILY_DB_NAME
            path = 'api/market/getMktFutd.json'
        elif unit == "weekly":
            DB_NAME = WEEKLY_DB_NAME
            path = 'api/market/getMktFutw.json'
        elif unit == "monthly":
            DB_NAME = MONTHLY_DB_NAME
            path = 'api/market/getMktFutm.json'
        else:
            print "参数错误"
            return

        # # 查询数据库中已有数据的最后日期
        # cl = self.dbClient[DB_NAME][symbol]
        # cx = cl.find(sort=[('datetime', pymongo.DESCENDING)])
        # if cx.count():
        #     last = cx[0]
        # else:
        #     last = ''

        # # 主力合约
        # if '0000' in symbol:
        #     path = 'api/market/getMktMFutd.json'
        #
        #     params = {}
        #     params['contractObject'] = symbol.replace('0000', '')
        #     params['mainCon'] = 1
        #     if last:
        #         params['startDate'] = last['date']
        # 交易合约

        params = {}
        params['ticker'] = symbol
        # if last:
        #     params['beginDate'] = last['date']

        # 开始下载数据
        data = self.datayesClient.downloadData(path, params)

        if data:
            print "返回 %d 条数据记录" % len(data)
            # 创建datetime索引
            self.dbClient[DB_NAME][symbol].ensure_index([('datetime', pymongo.ASCENDING)],
                                                              unique=True)

            for d in data:
                bar = CtaBarData()
                bar.vtSymbol = symbol
                bar.symbol = symbol
                try:
                    bar.exchange = DATAYES_TO_VT_EXCHANGE.get(d.get('exchangeCD', ''), '')
                    bar.open = d.get('openPrice', 0)
                    bar.high = d.get('highestPrice', 0)
                    bar.low = d.get('lowestPrice', 0)
                    bar.close = d.get('closePrice', 0)
                    if unit == "daily":
                        bar.date = d.get('tradeDate', '').replace('-', '')
                    else:
                        bar.date = d.get('endDate', '').replace('-', '')
                        bar.tradedays = d.get('tradeDays', 0)
                    bar.datetime = datetime.strptime(bar.date, '%Y%m%d')
                    bar.volume = d.get('turnoverVol', 0)
                    bar.openInterest = d.get('openInt', 0)
                except KeyError:
                    print d

                flt = {'datetime': bar.datetime}
                self.dbClient[DB_NAME][symbol].update_one(flt, {'$set': bar.__dict__}, upsert=True)

            print u'下载完成'
            return data
        else:
            print u'找不到合约%s' % symbol

    # ----------------------------------------------------------------------
    # def downloadAllFuturesDailyBar(self):
    #     """下载所有期货的主力合约日行情"""
    #     start = time()
    #     print u'开始下载所有期货的主力合约日行情'
    #
    #     productSymbolSet = self.readFuturesProductSymbol()
    #
    #     print u'代码列表读取成功，产品代码：%s' % productSymbolSet
    #
    #     # 这里也测试了线程池，但可能由于下载函数中涉及较多的数据格
    #     # 式转换，CPU开销较大，多线程效率并无显著改变。
    #     # p = ThreadPool(10)
    #     # p.map(self.downloadFuturesDailyBar, productSymbolSet)
    #     # p.close()
    #     # p.join()
    #
    #     for productSymbol in productSymbolSet:
    #         self.downloadFuturesDailyBar(productSymbol + '0000')
    #
    #     print u'所有期货的主力合约日行情已经全部下载完成, 耗时%s秒' % (time() - start)
    #
    # ----------------------------------------------------------------------
    def downloadFuturesIntradayBar(self, symbol, unit):
        """下载期货的日内分钟行情"""
        print u'开始下载%s日内分钟行情' % symbol

        # 日内分钟行情只有具体合约
        path = 'api/market/getFutureBarRTIntraDay.json'

        params = {}
        params['instrumentID'] = symbol
        params['unit'] = unit

        data = self.datayesClient.downloadData(path, params)
        # print u"datayesClient返回值: ", data

        if data:
            print "返回 %d 条数据记录" % len(data)
            today = datetime.now().strftime('%Y%m%d')
            yesterday = (datetime.now() - timedelta(1)).strftime('%Y%m%d')

            #给数据库命名
            dbname = ''
            if unit == 1:
                dbname = MINUTE_DB_NAME
            elif unit == 5:
                dbname = MINUTE5_DB_NAME
            elif unit == 15:
                dbname = MINUTE15_DB_NAME
            elif unit == 30:
                dbname = MINUTE30_DB_NAME
            elif unit == 60:
                dbname = MINUTE60_DB_NAME
            else:
                print u'分钟值错误'


            # 创建datetime索引
            self.dbClient[dbname][symbol].ensure_index([('datetime', pymongo.ASCENDING)],
                                                               unique=True)

            for d in data:
                if d.get('openPrice', 0) == d.get('highPrice', 0) == d.get('lowPrice', 0) == d.get('closePrice', 0):
                    continue
                else:
                    bar = CtaBarData()
                    bar.vtSymbol = symbol
                    bar.symbol = symbol
                    try:
                        bar.exchange = ''
                        bar.open = d.get('openPrice', 0)
                        bar.high = d.get('highPrice', 0)
                        bar.low = d.get('lowPrice', 0)
                        bar.close = d.get('closePrice', 0)
                        bar.time = d.get('barTime', '')
                        if bar.time[0] == '2':
                            bar.date = yesterday
                        else:
                            bar.date = today
                        bar.datetime = datetime.strptime(bar.date + ' ' + bar.time, '%Y%m%d %H:%M')
                        bar.volume = d.get('totalValue', 0)
                        bar.openInterest = d.get('openInterest', 0)
                    except KeyError:
                        print d

                    flt = {'datetime': bar.datetime}
                    self.dbClient[dbname][symbol].update_one(flt, {'$set': bar.__dict__}, upsert=True)

            print u'下载完成'
        else:
            print u'找不到合约%s' % symbol

            # ----------------------------------------------------------------------

    def downloadEquitySymbol(self, tradeDate=''):
        """下载所有股票的代码"""
        if not tradeDate:
            tradeDate = self.lastTradeDate()

        self.dbClient[SETTING_DB_NAME]['EquitySymbol'].ensure_index([('symbol', pymongo.ASCENDING)],
                                                                    unique=True)

        path = 'api/market/getMktEqud.json'

        params = {}
        params['tradeDate'] = tradeDate

        data = self.datayesClient.downloadData(path, params)

        if data:
            for d in data:
                symbolDict = {}
                symbolDict['symbol'] = d['ticker']
                flt = {'symbol': d['ticker']}

                self.dbClient[SETTING_DB_NAME]['EquitySymbol'].update_one(flt, {'$set': symbolDict},
                                                                          upsert=True)
            print u'股票代码下载完成'
        else:
            print u'股票代码下载失败'

    # ----------------------------------------------------------------------
    def downloadEquityDailyBar(self, symbol):
        """
        下载股票的日行情，symbol是股票代码
        """
        print u'开始下载%s日行情' % symbol

        # 查询数据库中已有数据的最后日期
        cl = self.dbClient[DAILY_DB_NAME][symbol]
        cx = cl.find(sort=[('datetime', pymongo.DESCENDING)])
        if cx.count():
            last = cx[0]
        else:
            last = ''

        # 开始下载数据
        path = 'api/market/getMktEqud.json'

        params = {}
        params['ticker'] = symbol
        if last:
            params['beginDate'] = last['date']

        data = self.datayesClient.downloadData(path, params)

        if data:
            # 创建datetime索引
            self.dbClient[DAILY_DB_NAME][symbol].ensure_index([('datetime', pymongo.ASCENDING)],
                                                              unique=True)

            for d in data:
                bar = CtaBarData()
                bar.vtSymbol = symbol
                bar.symbol = symbol
                try:
                    bar.exchange = DATAYES_TO_VT_EXCHANGE.get(d.get('exchangeCD', ''), '')
                    bar.open = d.get('openPrice', 0)
                    bar.high = d.get('highestPrice', 0)
                    bar.low = d.get('lowestPrice', 0)
                    bar.close = d.get('closePrice', 0)
                    bar.date = d.get('tradeDate', '').replace('-', '')
                    bar.time = ''
                    bar.datetime = datetime.strptime(bar.date, '%Y%m%d')
                    bar.volume = d.get('turnoverVol', 0)
                except KeyError:
                    print d

                flt = {'datetime': bar.datetime}
                self.dbClient[DAILY_DB_NAME][symbol].update_one(flt, {'$set': bar.__dict__}, upsert=True)

            print u'%s下载完成' % symbol
        else:
            print u'找不到合约%s' % symbol



            # ----------------------------------------------------------------------


def loadMcCsv(fileName, dbName, symbol):
    """将Multicharts导出的csv格式的历史数据插入到Mongo数据库中"""
    import csv

    start = time()
    print u'开始读取CSV文件%s中的数据插入到%s的%s中' % (fileName, dbName, symbol)

    # 锁定集合，并创建索引
    host, port = loadMongoSetting()

    client = pymongo.MongoClient(host, port)
    collection = client[dbName][symbol]
    collection.ensure_index([('datetime', pymongo.ASCENDING)], unique=True)

    # 读取数据和插入到数据库
    reader = csv.DictReader(file(fileName, 'r'))
    for d in reader:
        bar = CtaBarData()
        bar.vtSymbol = symbol
        bar.symbol = symbol
        bar.open = float(d['Open'])
        bar.high = float(d['High'])
        bar.low = float(d['Low'])
        bar.close = float(d['Close'])
        bar.date = datetime.strptime(d['Date'], '%Y/%m/%d').strftime('%Y%m%d')
        bar.time = d['Time']
        bar.datetime = datetime.strptime(bar.date + ' ' + bar.time, '%Y%m%d %H:%M:%S')
        bar.volume = d['TotalVolume']

        flt = {'datetime': bar.datetime}
        collection.update_one(flt, {'$set': bar.__dict__}, upsert=True)
        print bar.date, bar.time

    print u'插入完毕，耗时：%s' % (time() - start)


if __name__ == '__main__':
    ## 简单的测试脚本可以写在这里
    from time import sleep

    e = HistoryDataEngine()
    sleep(1)
    # e.downloadFuturesIntradayBar('ag1706', 1)
    # e.downloadEquityDailyBar('bu1703')
    # e.downloadFuturesSymbol()
    # e.downloadAllFuturesDailyBar()
    e.downloadFuturesBar("bu1706", "monthly")

    # 这里将项目中包含的股指日内分钟线csv导入MongoDB，作者电脑耗时大约3分钟
    # loadMcCsv('IF0000_1min.csv', MINUTE_DB_NAME, 'IF0000')
