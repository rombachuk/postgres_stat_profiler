from datetime import datetime
import base64
from cryptography.fernet import Fernet
import psycopg
from psycopg.rows import dict_row
import logging
from postgres_stat_profiler.collector.monitoredDatabase import monitoredDatabase
from postgres_stat_profiler.collector.reportDatabase import reportDatabase
from postgres_stat_profiler.models.cumulative_statstatements import cumulative_statstatements
from postgres_stat_profiler.models.incremental_statstatements import incremental_statstatements

class collector:

    def __init__(self,name, queryenc, querysecret, monitorconn,reportconn):
        self.profilename = name
        self.queryencryption = queryenc,
        self.queryencryptionsecret = querysecret,
        self.monitor_connection = monitorconn
        self.monitordb = monitoredDatabase(self.monitor_connection.getConnectionString())
        self.report_connection =  reportconn
        self.reportdb = reportDatabase(self.report_connection.getConnectionString())

    def getMonitoredDBstatus(self):
        return self.monitordb.getStatus()

    def getReportDBstatus(self):
        return self.reportdb.getStatus()

    def collect(self):
        if self.monitordb.getStatus() == u'operational' \
        and self.reportdb.getStatus() == u'initialised':
            try:
               now = datetime.now()
               rtime_minute = now.strftime('%Y-%m-%d %H:%M')
               rtime_epoch = int((datetime.strptime(rtime_minute,'%Y-%m-%d %H:%M') - datetime(1970, 1, 1)).total_seconds())
               if self.queryencryption:
                  secretbytes = base64.urlsafe_b64decode(self.queryencryptionsecret)          
                  fernetkey = base64.urlsafe_b64encode(secretbytes.ljust(32)[:32])
                  queryfernet = Fernet(fernetkey)
               else:
                  queryfernet = None

               mconn = psycopg.connect(self.monitordb.getConnstring(),row_factory=dict_row)
               rconn = psycopg.connect(self.reportdb.getConnstring(),row_factory=dict_row)

               # collect from monitored database and insert latest data into report database
               cumulativess = cumulative_statstatements()
               cumulative_collectquery = cumulativess.getCollectQuery()
               collectrecords = mconn.execute(cumulative_collectquery).fetchall()
               for collectrecord in collectrecords:
                   cumulative_insertquery = cumulativess.getInsertQuery()
                   cumulative_insertrecord = cumulativess.getInsertRecord(self.profilename,rtime_minute,rtime_epoch, \
                                                                          self.queryencryption,queryfernet,collectrecord)
                   result = rconn.execute(cumulative_insertquery, cumulative_insertrecord)
               rconn.commit()
               #logging.warning('pg-stat-profiler: cumulative statements collect success for [{}]'.format(rtime_minute))
               mconn.close()

               # compare latest and previous rows in cumulative table to generate incremental data (ie for activity within the last minute)
               incrementalss = incremental_statstatements()
               incremental_collectquery = incrementalss.getCollectQuery(self.profilename)
               incrementalrecords = rconn.execute(incremental_collectquery).fetchall()
               for incrementalrecord in incrementalrecords:
                   incremental_insertquery = incrementalss.getInsertQuery()
                   incremental_insertrecord = incrementalss.getInsertRecord(incrementalrecord)
                   result = rconn.execute(incremental_insertquery,incremental_insertrecord)
               rconn.commit()
               #logging.warning('pg-stat-profiler: incremental statements collect success for [{}]'.format(rtime_minute))
               rconn.close()

            except Exception as e:
               logging.warning('pg-stat-profiler: collector : collect error [{}]'.format(str(e)))