import logging
import logging.handlers
import base64
import time
from flask import request
from postgres_stat_profiler.config.connection import connection
from postgres_stat_profiler.collector.reportDatabase import reportDatabase
from postgres_stat_profiler.collector.monitoredDatabase import monitoredDatabase
from postgres_stat_profiler.collector.collector import collector

class profile:
  
  def __init__(self,data):
     self.valid = False
     self.queryencryption = u'disabled'
     self.queryencryptionsecret = u''
     if 'name' in data:
        self.name = data['name']
        if self._setStatuses(data) and self._setConnections(data):
           self.valid = True
     if 'queryencryption' in data and (data['queryencryption'] == u'enabled' or data['queryencryption'] == u'disabled' ) \
     and 'queryencryptionsecret' in data:
        self.queryencryption = data['queryencryption']
        self.queryencryptionsecret = data['queryencryptionsecret']

 
  def getName(self):
      return self.name
  
  def getStatus(self):
      return self.status
  
  def getMonitoredDBstatus(self):
      return self.monitordbstatus
     
  def getReportDBstatus(self): 
      return self.reportdbstatus
     
  def getValid(self):
     return self.valid
  
  # Do not call this method from api handlers. exposes secrets.
  # Use only for storing config persistence into (encrypted) file. 
  def getAllDetails(self):
     return self._getAllDetails(self.monitored_connection.getAllDetails(),self.report_connection.getAllDetails())
  
  def _getAllDetails(self,monitoredconndetails,reportconndetails):
     try: 
        return '{{ "name" : "{}", "status": "{}", "queryencryption" : "{}", "queryencryptionsecret" : "{}",\
           "monitored_connection" : {{{}}}, "monitordbstatus" : "{}", "report_connection" : {{{}}}, "reportdbstatus" : "{}" }}'.\
             format(self.name, self.status,self.queryencryption,self.queryencryptionsecret,\
             monitoredconndetails, self.getMonitoredDBstatus(),\
             reportconndetails, self.getReportDBstatus())
     except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-getDetails error : [{}]'.format(str(e)))

  # Use for api handlers
  # credentials and querysecret not exposed via this method
  def getApiDetails(self):
     return self._getApiDetails(self.monitored_connection.getApiDetails(),self.report_connection.getApiDetails())

  def _getApiDetails(self,monitoredconndetails,reportconndetails):
     try: 
        return '{{ "name" : "{}", "status": "{}", "queryencryption" : "{}",\
           "monitored_connection" : {{{}}}, "monitordbstatus": "{}", "report_connection" : {{{}}}, "reportdbstatus": "{}" }}'.\
             format(self.name, self.status,self.queryencryption,\
             monitoredconndetails, self.getMonitoredDBstatus(),\
             reportconndetails, self.getReportDBstatus())
     except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-getDetails error : [{}]'.format(str(e)))
     

  def update(self,data):
     try:
           errors = 0
           if data:
             if 'status' in data:
               if (data['status'] == u'disabled') or (data['status'] == u'enabled'):
                  self.status = data['status']
               else:
                  errors = errors + 1
             if 'queryencryption' in data:
                if (data['queryencryption'] == u'disabled') or (data['queryencryption'] == u'enabled'):
                  self.queryencryption = data['queryencryption']
                else:
                  errors = errors + 1
             if self.queryencryption == u'enabled' and 'queryencryptionsecret' in data:
               self.queryencryptionsecret = data['queryencryptionsecret']
             if 'report_connection' in data:
               self.report_connection.update(data['report_connection'])
             if 'reportdbstatus'in data:
               self.reportdbstatus = data['reportdbstatus']
             if 'monitored_connection' in data:
               self.monitored_connection.update(data['monitored_connection'])  
             if 'monitordbstatus'in data:
               self.monitordbstatus = data['monitordbstatus']
             if errors > 0:
                return False
             else: 
                self.valid = True
                return True      
     except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-update : [{}]'.format(str(e)))
        self.valid = False
        return False
     
  def run(self, profilesqueue, loggingqueue):
       try: 
        h = logging.handlers.QueueHandler(loggingqueue) 
        logger = logging.getLogger()
        logger.addHandler(h)
        logging.warn('pg_stat_profiler: profile data collector [{}] started'.format(self.name))
        sleep_interval = 60.0
        starttime = time.monotonic()
        while True:
           
           coll = collector(self.name,self.queryencryption,self.queryencryptionsecret,self.monitored_connection,self.report_connection)
           # report status to parent processes to allow api read of status      
           self.reportdbstatus = coll.getReportDBstatus()
           self.monitordbstatus = coll.getMonitoredDBstatus()
           # pass result back via queue to grandparent main process - which updates the (persistent, encrypted) profilesfile
           result = {"name": self.name, "reportdbstatus": self.reportdbstatus, "monitordbstatus": self.monitordbstatus }
           profilesqueue.put(result)
           coll.collect()
           time.sleep(sleep_interval - ((time.monotonic() - starttime) % sleep_interval))
           
       except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-run error : [{}]'.format(str(e)))
     
  def _setConnections(self,data):
     try:
           if data and 'report_connection' in data and 'monitored_connection' in data:
              self.report_connection = connection(data['report_connection'])
              self.monitored_connection = connection(data['monitored_connection'])
              if self.report_connection.getValid() and self.monitored_connection.getValid():
                    return True
           return False
     except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-setConnections error : [{}]'.format(str(e)))
        return False
         
  def _setStatuses(self,data):
     try:
           if data and 'status' in data :
              self.status = data['status']
           if not hasattr(self,'status'):
              self.status = 'disabled'
           if not hasattr(self,'monitordbstatus'):
              self.monitordbstatus = 'unknown'
           if not hasattr(self,'reportdbstatus'):
              self.reportdbstatus = 'unknown'
           return True
     except Exception as e:
        logging.warning('pg-stat-profiler : unexpected profile-setStatus error : [{}]'.format(str(e)))
        return False

  def __str__(self):
        return str(self.__dict__)  

