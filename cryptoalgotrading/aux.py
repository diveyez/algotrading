"""
aux.py

Aux functions needed to do some data manipulation, plot data, etc.
"""
import sys
from os import listdir, path, environ
import matplotlib as mpl
from subprocess import Popen, PIPE
from numpy import isnan
from pandas import read_csv, read_hdf, DataFrame
from time import time, localtime
from datetime import datetime, timedelta
import cryptoalgotrading.var as var
from cryptoalgotrading.finance import bollinger_bands
from cryptoalgotrading.lib_bittrex import Bittrex
from influxdb import InfluxDBClient
from binance.client import Client as Binance
from multiprocessing import cpu_count
import logging as log
from plyer import notification

if environ.get('DISPLAY', '') == '':
    print("No display found. Using non-interactive Agg backend")
    mpl.use('Agg')

mpl.rc('font', **{'family': 'sans-serif', 'sans-serif': ['Helvetica']})
# for Palatino and other serif fonts use:
# rc('font',**{'family':'serif','serif':['Palatino']})
mpl.rc('text', usetex=True)
mpl.rcParams['text.usetex'] = False

import matplotlib.pylab as plt

# plt.ion()

# plt.style.use('ggplot')

# Initiates log file.
log.basicConfig(filename=var.LOG_FILENAME,
                format='%(asctime)s - %(message)s',
                datefmt='%d/%m/%Y %H:%M:%S',
                level=log.DEBUG)


# Decorators
def timeit(method):
    """
    Decorator to measure functions duration.
    """

    def timed(*args, **kw):
        ts = time()
        result = method(*args, **kw)
        te = time()

        log.debug('Duration: %2.2f sec' % (te - ts))
        return result

    return timed


def safe(method):
    """
    Decorator to return safe in case of error.
    """

    def ret(*args, **kw):
        try:
            return method(*args, **kw)

        except Exception as e:
            log.exception(e)

        # return result

    return ret


def dropnan(method):
    """
    Decorator to return drop NaN from DataFrames.
    """

    def ret(*args, **kw):
        return method(*args, **kw).dropna()

    return ret


def connect_db():
    """
    Connects to Infludb.
    """

    # returning InfluxDBClient object.
    try:
        conn = InfluxDBClient(var.db_host,
                              var.db_port,
                              var.db_user,
                              var.db_password,
                              var.db_name)

    except Exception as e:
        log.exception(f"Unable to connect to DB: {e}")
        sys.exit(1)

    return conn


def get_markets_list(base='BTC',
                     exchange=var.default_exchange):
    """
    Gets all coins from a certain market.

    Args:
    - base: if you want just one market. Ex: BTC.
        Empty for all markets.

    Returns:
    - list of markets.
    - False if unsupported exchange.
    """

    ret = False

    if exchange == 'bittrex':
        try:
            bt = Bittrex('', '')
            log.debug("Connected to Bittrex.")
            ret = [i['MarketName'] for i in bt.get_markets()['result'] if i['MarketName'].startswith(base)]
        except Exception as e:
            log.exception(f"Unable to connect to Bittrex - {e}")

    elif exchange == 'binance':
        try:
            bnb = Binance('', '')
            log.debug("Connected to Binance.")
            ret = [i['symbol'] for i in bnb.get_all_tickers() if i['symbol'].endswith(base)]
        except Exception as e:
            log.exception(f"Unable to connect to Binance - {e}")
    return ret


def get_markets_on_files(interval, base='BTC'):
    """
    Gets all coins from a certain market, available on files.

    Args:
    - interval: data interval.
    - base: if you want just one market. Ex: BTC.
        Empty for all markets.

    Returns:
    - list of markets.
    """
    return [
        file_.split('.')[0]
        for file_ in listdir(f"{var.data_dir}/hist-{interval}")
        if file_.startswith(base)
    ]


# @dropnan
def get_historical_data(market,
                        interval=var.default_interval,
                        init_date=0,
                        end_date=0,
                        exchange=var.default_exchange):
    """
    Gets all historical data stored on DB, from a certain market.

    Args:
    - market: str with market.
    - interval: str with time between measures.
        Empty for default_interval.
    - init_date: str with initial datetime.
        Default is 2017-07-10 21:30:00.
    - end_date: str with end datetime.
    - exchange: str with exchange name.

    Returns:
    - market data in pandas.DataFrame.
    """
    verified_market = check_market_name(market, exchange)

    init_date = get_time_right(init_date) if init_date else '2018-02-02 00:00:00'
    time = "time > \'" + init_date + "\'"

    if end_date:
        end_date = get_time_right(end_date)
        time += " AND time < \'" + end_date + "\'"

    # Gets data from Bittrex exchange.
    if exchange == 'binance':
        command = "SELECT last(Last) AS Last," + \
                  " last(BaseVolume) AS BaseVolume," + \
                  " last(High) AS High," + \
                  " last(Low) AS Low," + \
                  " last(Ask) AS Ask," + \
                  " last(Bid) AS Bid " + \
                  "FROM binance WHERE " + time + \
                  " AND MarketName='" + verified_market + \
                  "' GROUP BY time(" + interval + ")"

    elif exchange == 'bittrex':
        command = "SELECT last(Last) AS Last," + \
                  " last(BaseVolume) AS BaseVolume," + \
                  " last(High) AS High," + \
                  " last(Low) AS Low," + \
                  " last(Ask) AS Ask," + \
                  " last(Bid) AS Bid," + \
                  " last(OpenBuyOrders) AS OpenBuy," + \
                  " last(OpenSellOrders) AS OpenSell " + \
                  "FROM bittrex WHERE " + time + \
                  " AND MarketName='" + verified_market + \
                  "' GROUP BY time(" + interval + ")"

    db_client = connect_db()

    res = db_client.query(command)

    db_client.close()

    # returning Pandas DataFrame.
    return detect_init(DataFrame(list(res.get_points(measurement=exchange))))


def get_last_data(market,
                  last='24',
                  interval=var.default_interval,
                  exchange=var.default_exchange,
                  db_client=0):
    """
    Gets last data from DB.

    Args:
    - market: str with market.
    - last: int with number of hours from now to get.
        Empty for 24 hours.
    - interval: str with time between measures.
        Empty for default_interval.

    Returns:
    - market data in pandas.DataFrame.
    """

    end_date = 'now()'

    # date and time format> 2018-02-02 00:00:00
    start_date = format(datetime.now() -
                        timedelta(hours=last),
                        '%Y-%m-%d %H:%M:%S')

    return get_historical_data(market,
                               interval=interval,
                               init_date=start_date,
                               end_date=end_date,
                               exchange=exchange)


def detect_init(data):
    """
    Remove data without info in case of market
    has started after the implementation of the BD.
    """

    # TODO implement this on DB query.
    for i in range(len(data)):
        # TODO remove numpy lib and use other method to detect NaN.
        if not isnan(data.Last.iloc[i]):
            return data[i:]


def plot_data(data,
              name='',
              date=None,
              smas=var.default_smas,
              emas=var.default_emas,
              entry_points=None,
              exit_points=None,
              to_file=False,
              show_smas=False,
              show_emas=False,
              show_bbands=False):
    """
    Plots selected data.
    entry_points is a tuple of lists: (entry_points_x,entry_points_y)
    """
    # plt.clf()

    # For when it's called outside backtest.
    if date is None:
        date = [0, 0]
    if date != [0, 0] and len(data) != date[1] - date[0]:
        data = data[date[0]:date[1]]

    f, (ax1, ax2, ax3) = plt.subplots(3,
                                      sharex='all',
                                      figsize=(9, 4),
                                      gridspec_kw={'height_ratios': [3, 1, 1]})

    ax1.grid(True)
    ax2.grid(True)
    ax3.grid(True)

    # var date is causing conflicts. using name date.
    end_date = len(data) if date[1] == 0 else date[1]
    x = range(date[0], end_date)
    ax1.plot(x, data.Last, color='black', linewidth=1, alpha=0.65)

    if show_bbands:
        bb_upper, bb_lower, bb_sma = bollinger_bands(data.Last, 10, 2)
        # ax1.plot(x, bb_upper, color='red', linestyle='none', linewidth=1)
        # ax1.plot(x, bb_lower, color='green', linestyle='none', linewidth=1)

        ax1.fill_between(x, bb_sma, bb_upper, color='green', alpha=0.3)
        ax1.fill_between(x, bb_lower, bb_sma, color='red', alpha=0.3)

    if show_smas:
        for sma in smas:
            ax1.plot(x, data.Last.rolling(sma).mean())

    if show_emas:
        for ema in emas:
            ax1.plot(x, data.Last.ewm(ema).mean())

    if entry_points:
        ax1.plot(entry_points[0],
                 entry_points[1],
                 marker='o',
                 linestyle='None',
                 color='green',
                 alpha=0.55)
    if exit_points:
        ax1.plot(exit_points[0],
                 exit_points[1],
                 marker='o',
                 linestyle='None',
                 color='red',
                 alpha=0.45)
    ax2.set_ylim((data.BaseVolume.min() - 1,
                  data.BaseVolume.max() + 1))
    ax2.bar(x, data.BaseVolume.iloc[:], 1, color='black', alpha=0.55)

    try:
        ax3.plot(x, data.OpenSell.iloc[:])
    except Exception:
        ax3.plot(x, data.High.iloc[:])

    plt.xlim(date[0], end_date)
    plt.tight_layout()
    f.subplots_adjust(hspace=0)
    if to_file:
        if not name:
            name = f'fig_test{str(time())}'
        f.savefig(f"{var.fig_dir}{name}-{str(time())}.pdf", bbox_inches='tight')
        plt.close(f)

    return True


def get_histdata_to_file(markets=None,
                         interval=var.default_interval,
                         date_=None,
                         base_market='BTC',
                         exchange=var.default_exchange,
                         file_name=None,
                         filetype='csv'):
    """
    Gets data from DB to file.
    Prevents excess of DB accesses.
    Saves files to 'hist-<interval>.csv'

    Args:
    - market: list of str with markets.
    - interval: str with time between measures.
        Empty for default_interval.
    - date_: list of str with init and end date.
        Default is [0,0] for all data.
    - base_market: str with base market.
        Default is BTC.
    - exchange: str with crypto exchange.
        Default is var.default_exchange.
    - filetype: str with filetype to save.
        Default is csv

    Returns:
    - 'True'
    """

    if date_ is None:
        date_ = [0, 0]
    if isinstance(markets, str): markets = [markets]

    if not markets:
        markets = get_markets_list(base_market, exchange)

    for market in markets:
        verified_market = check_market_name(market, exchange=exchange)
        log.debug(verified_market)

        data_ = get_historical_data(verified_market,
                                    interval=interval,
                                    init_date=date_[0],
                                    end_date=date_[1],
                                    exchange=exchange)

        if not file_name:
            file_name = var.data_dir + '/hist-' + \
                        interval + '/' + \
                        verified_market
        file_name += '.'

        if not isinstance(data_, DataFrame):
            log.error("Unable to get data")
            return False

        if filetype == 'csv':
            data_.to_csv(f"{file_name}{filetype}")
        elif filetype == 'hdf':
            data_.to_hdf(f"{file_name}{filetype}", 'data',
                         mode='w', format='f',
                         complevel=9, complib='bzip2')
        # TEST
        del data_
        log.info(f"{file_name}{filetype} downloaded.")

    return True


# Use it if you got too much NaN in your data.
# Will make your func slower!
@dropnan
def get_data_from_file(market,
                       interval=var.default_interval,
                       exchange=var.default_exchange,
                       filetype='csv'):
    """
    Gets data from file.

    Args:
    - market: str with market.
    - interval: str with time between measures.
        Empty for default_interval.
    - exchange: str with crypto exchange.
        bittrex as default exchange.
    - filetype: str with filetype.
        default type is csv.

    Returns:
    - pd.DataFrame
    """
    verified_market = check_market_name(market, exchange=exchange)

    filename_ = var.data_dir + '/hist-' + interval + \
                '/' + verified_market + '.' + filetype

    if filetype == 'csv':
        return read_csv(filename_, sep=',', engine='c', index_col=0)  # Optimized.
    elif filetype == 'hdf':
        return read_hdf(filename_, 'data')
    else:
        return 0


def check_market_name(market,
                      exchange=var.default_exchange):
    """
    Avoids abbreviations and lower cases failures.
    """
    market = market.upper()

    if exchange == 'binance':  # and len(market) > 5:
        return market
    elif exchange == 'bittrex':
        if '-' in market:  # and len(market) > 5:
            return market
        return 'BTC-' + market


def time_to_index(data, _datetime):
    """
    Converts input time to DB time.

    What time_to_index is expecting:
        '01-01-2017 11:10'

    Returns:
        2017-09-09T06:25:00Z

    # TODO - Improve date presentation
    """

    # d[(d.time>'2017-09-09T06:25:00Z') & (d.time<'2017-09-09T07:25:00Z')]

    # year, month, day = time.strftime("%Y,%m,%d").split(',')
    dtime = []

    for t in _datetime:

        if ' ' in t:
            t_date, t_time = t.split()
        else:
            t_date = t
            t_time = '00:00'

        try:
            t_day, t_month, t_year = t_date.split('-')
        except Exception:
            t_day, t_month = t_date.split('-')
            t_year = localtime(time())[0]

        t_hour, t_minute = t_time.split(':')

        dtime.append(
            (
                (
                    (
                        ((f'{str(t_year)}-' + str(t_month)) + '-')
                        + str(t_day)
                        + 'T'
                    )
                    + str(t_hour)
                    + ':'
                )
                + str(t_minute)
                + ':00Z'
            )
        )


    try:
        d = data[(data.time > dtime[0]) & (data.time < dtime[1])]
    except Exception as e:
        log.exception(f"{e}")
        return 0, 0

    return d.index[0], d.index[-1]


def get_time_right(date_n_time):
    if ' ' in date_n_time:
        t_date, t_time = date_n_time.split()
    else:
        t_date = date_n_time
        t_time = '00:00'

    if '-' in date_n_time:
        try:
            t_day, t_month, t_year = t_date.split('-')
        except Exception:
            t_day, t_month = t_date.split('-')
            t_year = str(localtime()[0])

    elif '/' in date_n_time:
        try:
            t_day, t_month, t_year = t_date.split('/')
        except Exception:
            t_day, t_month = t_date.split('/')
            t_year = str(localtime()[0])

    t_hour, t_minute = t_time.split(':')

    return t_year + '-' + \
           t_month + '-' + \
           t_day + 'T' + \
           t_hour + ':' + \
           t_minute + ':00Z'


def trailing_stop_loss(last,
                       higher,
                       percentage=var.trailing_loss_prcnt):
    """
    Trailing stop loss function.

    Receives structure with:
        - Last price.
        - Entry point x.
        - Exit percentage [0.1-99.9]

    Returns true when triggered.
    """

    return last <= higher * (1 - (percentage * 0.01))


def stop_loss(last,
              entry_point_x,
              percentage=var.stop_loss_prcnt):
    """
    Stop loss function.
        
    Receives structure with:
        - Last price.
        - Entry point x.
    
    Returns true when triggered.
    """

    return last <= entry_point_x * (1 - (percentage * 0.01))


def num_processors(level="medium"):
    """
    Decides how many cores will use.

    level options:
        low             = 1 core
        medium          = half of available cores.
        high            = left 1 free core.
        max|extreme     = uses all available cores.
        <cores number>  = uses the number of cores specified.
    """

    mp = cpu_count()

    if level == "low":
        n_threads = 1
    elif level == "high":
        n_threads = mp - 1
    elif level in ["extreme", "max"]:
        n_threads = mp
    elif isinstance(level, int) and 0 < level <= mp:
        n_threads = level
    else:
        n_threads = int(mp / 2)

    log.debug(f"Using {n_threads} threads.")
    return n_threads


def beep(duration=0.5):
    """
    It beeps!
    Used to alert for possible manual entry or exit.
    """
    freq = 440  # Hz
    
    # Play need to be installed.
    _, err = run_command(f"play --no-show-progress --null --channels 1 synth {duration} sine {freq}")

    return err


def desktop_notification(content: dict):
    """
    Presents a message on desktop.
    :param content: dictionary with notification info
    """
    # TODO - add timer

    icon = ''

    if var.desktop_cool_mode and content['type'] == 'P&L':
        icon = var.img_profit if content['profit'] > 0 else var.img_loss
    notification.notify(
        title = content['title'],
        message = content['message'],
        app_name = 'CAT',
        app_icon = icon
    )
    return 0


def manage_files(markets, interval='1m'):
    """
    Manage market files in order to improve framework performance.
    """
    all_files = []
    markets_name = [check_market_name(market) for market in markets]

    if not path.isdir(f"{var.data_dir}/hist-{interval}"):
        log.error(f"{var.data_dir}/hist-{interval} doesn't exist.")
        sys.exit(1)

    for f in listdir(f"{var.data_dir}/hist-{interval}"):
        all_files.extend(
            f.split('.')[0] for market in markets_name if f.startswith(market)
        )

    return all_files


def file_lines(filename):
    """
    Counts the number of lines in a file
    """

    with open(filename) as f:
        lines = 0
        buf_size = 1024 * 1024
        read_f = f.read  # loop optimization

        while buf := read_f(buf_size):
            lines += buf.count('\n')
    return lines


def binance2btrx(_data):
    """
    Converts Binance data structure into Bittrex model.
    """

    return {'MarketName': str(_data['symbol']),
                'Ask': float(_data['askPrice']),
                'BaseVolume': float(_data['quoteVolume']),
                'Bid': float(_data['bidPrice']),
                'High': float(_data['highPrice']),
                'Last': float(_data['lastPrice']),
                'Low': float(_data['lowPrice']),
                'Volume': float(_data['volume']),
                'Count': float(_data['count'])}


def run_command(cmd):
    """
    Run a command in terminal through Python
    """
    process = Popen(cmd.split(),
                    stdout=PIPE,
                    stderr=PIPE)
    stdout, _ = process.communicate()
    out = stdout.decode("utf-8").replace('\t', '')\
                                .replace(': ', ':')\
                                .splitlines()
    return out, process.returncode
