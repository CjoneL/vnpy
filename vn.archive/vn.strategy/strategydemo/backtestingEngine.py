# encoding: UTF-8

import shelve
import MySQLdb

from eventEngine import *
from pymongo import Connection
from pymongo.errors import *

from strategyEngine import *


########################################################################
class LimitOrder(object):
    """限价单对象"""

    #----------------------------------------------------------------------
    def __init__(self, symbol):
        """Constructor"""
        self.symbol = symbol
        self.price = 0
        self.volume = 0
        self.direction = None
        self.offset = None


########################################################################
class BacktestingEngine(object):
    """
    回测引擎，作用：
    1. 从数据库中读取数据并回放
    2. 作为StrategyEngine创建时的参数传入
    """

    #----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.eventEngine = EventEngine()
        
        # 策略引擎
        self.strategyEngine = None
        
        # TICK历史数据列表，由于要使用For循环来实现仿真回放
        # 使用list的速度比Numpy和Pandas都要更快
        self.listDataHistory = []
        
        # 限价单字典
        self.dictOrder = {}
        
        # 最新的TICK数据
        self.currentData = None
        
        # 回测的成交字典
        self.listTrade = []
        
        # 报单编号
        self.orderRef = 0
        
        # 成交编号
        self.tradeID = 0

        # 回测编号
        self.Id = datetime.now().strftime('%Y%m%d-%H%M%S')

        # 回测对象
        self.symbol = ''

        # 回测开始日期
        self.startDate = None

        # 回测结束日期
        self.endDate = None

        self.minDiff = EMPTY_FLOAT

    def setMinDiff(self, minDiff):

        self.minDiff = minDiff
        
    #----------------------------------------------------------------------
    def setStrategyEngine(self, engine):
        """设置策略引擎"""
        self.strategyEngine = engine
        self.writeLog(u'策略引擎设置完成')
    
    #----------------------------------------------------------------------
    def connectMongo(self):
        """连接MongoDB数据库"""
        try:
            self.__mongoConnection = Connection()
            self.__mongoConnected = True
            self.__mongoTickDB = self.__mongoConnection['TickDB']
            self.writeLog(u'回测引擎连接MongoDB成功')
        except ConnectionFailure:
            self.writeLog(u'回测引擎连接MongoDB失败') 

    #----------------------------------------------------------------------
    def loadMongoDataHistory(self, symbol, startDate, endDate):
        """载入历史TICK数据"""
        if self.__mongoConnected:
            collection = self.__mongoTickDB[symbol]
            
            # 如果输入了读取TICK的最后日期
            if endDate:
                cx = collection.find({'date':{'$gte':startDate, '$lte':endDate}})
            elif startDate:
                cx = collection.find({'date':{'$gte':startDate}})
            else:
                cx = collection.find()
            
            # 将TICK数据读入内存
            self.listDataHistory = [data for data in cx]
            
            self.writeLog(u'历史TICK数据载入完成')
        else:
            self.writeLog(u'MongoDB未连接，请检查')

    #----------------------------------------------------------------------
    def connectMysql(self):
        """连接MysqlDB"""

        # 载入json文件
        fileName = 'mysql_connect.json'
        try:
            f = file(fileName)
        except IOError:
            self.writeLog(u'回测引擎读取Mysql_connect.json失败')
            return

        # 解析json文件
        setting = json.load(f)
        try:
            mysql_host = str(setting['host'])
            mysql_port = int(setting['port'])
            mysql_user = str(setting['user'])
            mysql_passwd = str(setting['passwd'])
            mysql_db = str(setting['db'])


        except IOError:
            self.writeLog(u'回测引擎读取Mysql_connect.json,连接配置缺少字段，请检查')
            return

        try:
            self.__mysqlConnection = MySQLdb.connect(host=mysql_host, user=mysql_user,
                                                     passwd=mysql_passwd, db=mysql_db, port=mysql_port)
            self.__mysqlConnected = True
            self.writeLog(u'回测引擎连接MysqlDB成功')
        except ConnectionFailure:
            self.writeLog(u'回测引擎连接MysqlDB失败')
    #----------------------------------------------------------------------
    def setDataHistory(self, symbol, startDate, endDate):
        """设置Tick历史数据的加载要求"""
        self.symbol = symbol
        self.startDate = startDate
        self.endDate = endDate


    #----------------------------------------------------------------------
    def loadDataHistory(self, symbol, startDate, endDate):
        """载入历史TICK数据
        如果加载过多数据会导致加载失败,间隔不要超过半年
        """

        if not endDate:
            endDate = datetime.today()

        # 看本地缓存是否存在
        if self.__loadDataHistoryFromLocalCache(symbol, startDate, endDate):
            self.writeLog(u'历史TICK数据从Cache载入')
            return

        # 每次获取日期周期
        intervalDays = 10

        for i in range (0,(endDate - startDate).days +1, intervalDays):
            d1 = startDate + timedelta(days = i )

            if (endDate - d1).days > 10:
                d2 = startDate + timedelta(days = i + intervalDays -1 )
            else:
                d2 = endDate

            # 从Mysql 提取数据
            self.loadMysqlDataHistory(symbol, d1, d2)

        self.writeLog(u'历史TICK数据共载入{0}条'.format(len(self.listDataHistory)))

        # 保存本地cache文件
        self.__saveDataHistoryToLocalCache(symbol, startDate, endDate)


    def __loadDataHistoryFromLocalCache(self, symbol, startDate, endDate):
        """看本地缓存是否存在"""

        # 运行路径下cache子目录
        cacheFolder = os.getcwd()+'/cache'

        # cache文件
        cacheFile = u'{0}/{1}_{2}_{3}.pickle'.\
                    format(cacheFolder, symbol, startDate.strftime('%Y-%m-%d'), endDate.strftime('%Y-%m-%d'))

        if not os.path.isfile(cacheFile):
            return False

        else:
            # 从cache文件加载
            cache = open(cacheFile,mode='r')
            self.listDataHistory = cPickle.load(cache)
            cache.close()
            return True

    def __saveDataHistoryToLocalCache(self, symbol, startDate, endDate):
        """保存本地缓存"""

        # 运行路径下cache子目录
        cacheFolder = os.getcwd()+'/cache'

        # 创建cache子目录
        if not os.path.isdir(cacheFolder):
            os.mkdir(cacheFolder)

        # cache 文件名
        cacheFile = u'{0}/{1}_{2}_{3}.pickle'.\
                    format(cacheFolder, symbol, startDate.strftime('%Y-%m-%d'), endDate.strftime('%Y-%m-%d'))

        # 重复存在 返回
        if os.path.isfile(cacheFile):
            return False

        else:
            # 写入cache文件
            cache = open(cacheFile, mode='w')
            cPickle.dump(self.listDataHistory,cache)
            cache.close()
            return True

    #----------------------------------------------------------------------
    def loadMysqlDataHistory(self, symbol, startDate, endDate):
        """从Mysql载入历史TICK数据,"""
        #Todo :判断开始和结束时间，如果间隔天过长，数据量会过大，需要批次提取。
        try:
            self.connectMysql()
            if self.__mysqlConnected:


                # 获取指针
                cur = self.__mysqlConnection.cursor(MySQLdb.cursors.DictCursor)

                if endDate:

                    # 开始日期 ~ 结束日期
                    sqlstring = ' select \'{0}\' as InstrumentID, str_to_date(concat(ndate,\' \', ntime),' \
                               '\'%Y-%m-%d %H:%i:%s\') as UpdateTime,price as LastPrice,vol as Volume,' \
                               'position_vol as OpenInterest,bid1_price as BidPrice1,bid1_vol as BidVolume1, ' \
                               'sell1_price as AskPrice1, sell1_vol as AskVolume1 from TB_{0}MI ' \
                               'where ndate between cast(\'{1}\' as date) and cast(\'{2}\' as date) order by UpdateTime'.\
                               format(symbol,  startDate, endDate)

                elif startDate:

                    # 开始日期 - 当前
                    sqlstring = ' select \'{0}\' as InstrumentID,str_to_date(concat(ndate,\' \', ntime),' \
                               '\'%Y-%m-%d %H:%i:%s\') as UpdateTime,price as LastPrice,vol as Volume,' \
                               'position_vol as OpenInterest,bid1_price as BidPrice1,bid1_vol as BidVolume1, ' \
                               'sell1_price as AskPrice1, sell1_vol as AskVolume1 from TB__{0}MI ' \
                               'where ndate > cast(\'{1}\' as date) order by UpdateTime'.\
                               format( symbol, startDate)

                else:

                    # 所有数据
                    sqlstring =' select \'{0}\' as InstrumentID,str_to_date(concat(ndate,\' \', ntime),' \
                              '\'%Y-%m-%d %H:%i:%s\') as UpdateTime,price as LastPrice,vol as Volume,' \
                              'position_vol as OpenInterest,bid1_price as BidPrice1,bid1_vol as BidVolume1, ' \
                              'sell1_price as AskPrice1, sell1_vol as AskVolume1 from TB__{0}MI order by UpdateTime'.\
                              format(symbol)

                #self.writeCtaLog(sqlstring)

                # 执行查询
                count = cur.execute(sqlstring)
                #self.writeCtaLog(u'历史TICK数据共{0}条'.format(count))

                # 将TICK数据一次性读入内存
                #self.listDataHistory = cur.fetchall()

                # 分批次读取
                fetch_counts = 0
                fetch_size = 1000

                while True:
                    results = cur.fetchmany(fetch_size)

                    if not results:
                        break

                    fetch_counts = fetch_counts + len(results)

                    if not self.listDataHistory:
                        self.listDataHistory =results

                    else:
                        self.listDataHistory =  self.listDataHistory + results

                    self.writeLog(u'{1}~{2}历史TICK数据载入共{0}条'.format(fetch_counts,startDate,endDate))


            else:
                self.writeLog(u'MysqlDB未连接，请检查')

        except MySQLdb.Error, e:

            self.writeLog(u'MysqlDB载入数据失败，请检查.Error {0}'.format(e))

    #----------------------------------------------------------------------
    def getMysqlDeltaDate(self,symbol, startDate, decreaseDays):
        """从mysql库中获取交易日前若干天"""
        try:
            if self.__mysqlConnected:

                # 获取mysql指针
                cur = self.__mysqlConnection.cursor()

                sqlstring='select distinct ndate from TB_{0}MI where ndate < ' \
                          'cast(\'{1}\' as date) order by ndate desc limit {2},1'.format(symbol, startDate, decreaseDays-1)

                # self.writeCtaLog(sqlstring)

                count = cur.execute(sqlstring)

                if count > 0:

                    # 提取第一条记录
                    result = cur.fetchone()

                    return result[0]

                else:
                    self.writeLog(u'MysqlDB没有查询结果，请检查日期')

            else:
                self.writeLog(u'MysqlDB未连接，请检查')

        except MySQLdb.Error, e:

            self.writeLog(u'MysqlDB载入数据失败，请检查.Error {0}: {1}'.format(e.arg[0],e.arg[1]))

        # 出错后缺省返回
        return startDate-timedelta(days=3)

    #----------------------------------------------------------------------
    def processLimitOrder(self):
        """处理限价单"""

        for ref, order in self.dictOrder.items():

            # 设置商品最少价格单位，防止数据中出现askprice=0，bidprice=0
            askPrice1 = float(self.currentData['AskPrice1'])
            if askPrice1 ==EMPTY_FLOAT:
                askPrice1 = float(self.currentData['LastPrice'])+self.minDiff

            bidPrice1 = float(self.currentData['BidPrice1'])
            if bidPrice1 == EMPTY_FLOAT:
                bidPrice1 = float(self.currentData['LastPrice']) - self.minDiff


            # 如果是买单，且限价大于等于当前TICK的卖一价，则假设成交
            if order.direction == DIRECTION_BUY and \
               order.price >= self.currentData['AskPrice1']:
                self.executeLimitOrder(ref, order, self.currentData['AskPrice1'])
            # 如果是卖单，且限价低于当前TICK的买一价，则假设全部成交
            if order.direction == DIRECTION_SELL and \
               order.price <= self.currentData['BidPrice1']:
                self.executeLimitOrder(ref, order, self.currentData['BidPrice1'])
    
    #----------------------------------------------------------------------
    def executeLimitOrder(self, ref, order, price):
        """限价单成交处理"""
        # 成交回报
        self.tradeID = self.tradeID + 1
        
        tradeData = {}
        tradeData['InstrumentID'] = order.symbol
        tradeData['OrderRef'] = ref
        tradeData['TradeID'] = str(self.tradeID)
        tradeData['Direction'] = order.direction
        tradeData['OffsetFlag'] = order.offset
        tradeData['Price'] = price
        tradeData['Volume'] = order.volume
        
        tradeEvent = Event()
        tradeEvent.dict_['data'] = tradeData
        self.strategyEngine.updateTrade(tradeEvent)
        
        # 报单回报
        orderData = {}
        orderData['InstrumentID'] = order.symbol
        orderData['OrderRef'] = ref
        orderData['Direction'] = order.direction
        orderData['CombOffsetFlag'] = order.offset
        orderData['LimitPrice'] = price
        orderData['VolumeTotalOriginal'] = order.volume
        orderData['VolumeTraded'] = order.volume
        orderData['InsertTime'] = ''
        orderData['CancelTime'] = ''
        orderData['FrontID'] = ''
        orderData['SessionID'] = ''
        orderData['OrderStatus'] = ''
        
        orderEvent = Event()
        orderEvent.dict_['data'] = orderData
        self.strategyEngine.updateOrder(orderEvent)
        
        # 记录该成交到列表中
        self.listTrade.append(tradeData)
        
        # 删除该限价单
        del self.dictOrder[ref]
                
    #----------------------------------------------------------------------
    def startBacktesting(self):
        """开始回测"""
        self.writeLog(u'开始回测')
        
        for data in self.listDataHistory:
            # 记录最新的TICK数据
            self.currentData = data
            
            # 处理限价单
            self.processLimitOrder()
            
            # 推送到策略引擎中
            event = Event()
            event.dict_['data'] = data
            self.strategyEngine.updateMarketData(event)
            
        self.saveTradeData()
        
        self.writeLog(u'回测结束')
    
    #----------------------------------------------------------------------
    def sendOrder(self, instrumentid, exchangeid, price, pricetype, volume, direction, offset):
        """回测发单"""
        order = LimitOrder(instrumentid)
        order.price = price
        order.direction = direction
        order.volume = volume
        order.offset = offset
        
        self.orderRef = self.orderRef + 1
        self.dictOrder[str(self.orderRef)] = order
        
        return str(self.orderRef)
    
    #----------------------------------------------------------------------
    def cancelOrder(self, instrumentid, exchangeid, orderref, frontid, sessionid):
        """回测撤单"""
        try:
            del self.dictOrder[orderref]
        except KeyError:
            pass
        
    #----------------------------------------------------------------------
    def writeLog(self, log):
        """写日志"""
        print log
        
    #----------------------------------------------------------------------
    def selectInstrument(self, symbol):
        """读取合约数据"""
        d = {}
        d['ExchangeID'] = 'BackTesting'
        return d
    
    #----------------------------------------------------------------------
    def saveTradeData(self):
        """保存交易记录"""
        f = shelve.open('result.vn')
        f['listTrade'] = self.listTrade
        f.close()
        
    #----------------------------------------------------------------------
    def subscribe(self, symbol, exchange):
        """仿真订阅合约"""
        pass
        
    
    