import sys
import os
import logging
import logging.handlers
import time
from flask import Flask, abort, jsonify, make_response, request, Response
from flask_apscheduler import APScheduler
from functools import wraps
import multiprocessing
from postgres_stat_profiler.api_auth.api_request import api_request
from postgres_stat_profiler.api_auth.api_keystore import api_keystore
from postgres_stat_profiler.config.profilestore import profilestore
from postgres_stat_profiler.collection.collectorsupervisor import collectorsupervisor
from postgres_stat_profiler.helpers.env_helper import fetch_env_allow_empty



# log listener which handles all child process logging to the common log file destination
# note; logging to same file direct from many child processes is not safe
# this runs in a child process supervised by check_slavejobs
#
def log_listener(file, queue):
    listenerlogger = logging.getLogger()
    h = logging.handlers.RotatingFileHandler(file, 'a', 100000000, 10)
    f = logging.Formatter('%(asctime)s[%(funcName)-5s] (%(processName)-10s) %(message)s')
    h.setFormatter(f)
    listenerlogger.addHandler(h)
    logging.warning('pg_stat-profiler: loglistener started')
    while True:
      while not queue.empty():
        try:
            record = queue.get()
            if record is None:  # We send this as a sentinel to tell the listener to quit.
                break
            listenerlogger.handle(record)  # No level or filter logic applied - just do it!
        except Exception as e:
            print('pg-stat-profiler : loglistener : Unexpected error [{}]'.format(str(e)))
      time.sleep(0.1)

# profile supervisor control function: runs periodically in parallel with Flask
#
def check_slavejobs(loggingjob,loggingqueue,logfilename,supervisorjob,profilesqueue,supervisor,profile_store):
  try:
   # 
   if not supervisorjob.is_alive():
      supervisorjob.join()
      supervisorjob = multiprocessing.Process(target=supervisor.run,args=(profilesqueue,loggingqueue))
      supervisorjob.start()
      logging.warning("pg-stat-profiler: supervisor restarted with processid :[{}]".format(str(supervisorjob.pid)))
   else:
     #  handle profile state changes passed from individual collectors up via supervisor
     #  ensures that only this main thread updates the persistent profilesfilestore
     while not profilesqueue.empty(): 
        qdata = profilesqueue.get()
        if 'name' in qdata:
           profile_store.updateProfile(qdata['name'],qdata)
   #
   # restart loglistener if it fails
   if not loggingjob.is_alive():
      loggingjob.join()
      loggingjob = multiprocessing.Process(target=log_listener,args=(logfilename,loggingqueue))
      loggingjob.start()
      logging.warning("pg-stat-profiler: loglistener restarted with processid :[{}]".format(str(loggingjob.pid)))

  except Exception as e:
      logging.warning("pg-stat-profiler: Error checking supervisor and loglistener :[{}]".format(str(e)))


   
def create_app():
 
  app = Flask(__name__)
  scheduler = APScheduler()
  scheduler.init_app(app)
  scheduler.start()

  # environment checks - fail to start if missing or not expandable
  installbase = fetch_env_allow_empty(u'PG_STAT_PROFILER_BASE')
  secbase = os.path.join(installbase,u'postgres_stat_profiler/resources/sec')
  if not os.path.isdir(secbase):
   print('pg-stat-profiler: Failed to find security directory, exiting...')
   print('pg-stat-profiler: Check [Invalid Install base={}]'.format(str(installbase)))
   print('pg-stat-profiler: Please supply correct environment variable PG_STAT_PROFILER_BASE'.format(str(installbase)))
   sys.exit()

  logbase = fetch_env_allow_empty(u'PG_STAT_PROFILER_LOGBASE')
  if not os.path.isdir(logbase):
   print('pg-stat-profiler: Failed to initiate logging, exiting...')
   print('pg-stat-profiler: Reason [Invalid Environment Variable PG_STAT_PROFILER_LOGBASE={}]'.format(str(logbase)))
   sys.exit()

  apiconfig_secret = os.getenv(u'PG_STAT_PROFILER_CONFIG_SECRET')
  if not apiconfig_secret:  
      print("pg-stat-profiler: Failed to find secret, exiting...")
      print("Failed to find environment variable PG_STAT_PROFILER_CONFIG_SECRET")
      sys.exit()

  apikeygen_secret = os.getenv(u'PG_STAT_PROFILER_APIKEYGEN_SECRET')
  if not apikeygen_secret:  
      print("pg-stat-profiler: Failed to find secret, exiting...")
      print("Failed to find environment variable PG_STAT_PROFILER_APIKEYGEN_SECRET")
      sys.exit()

  try:
      # set up logging for this main process
      logfilename = os.path.join(logbase,u'pg-stat-profiler.log')
      mainlogger = logging.getLogger()
      h = logging.handlers.RotatingFileHandler(logfilename, 'a', 100000000, 10)
      f = logging.Formatter('%(asctime)s[%(funcName)-5s] (%(processName)-10s) %(message)s')
      h.setFormatter(f)
      mainlogger.addHandler(h)
      # set up logging to same file for child processes (via the loggingjob child)
      loggingqueue = multiprocessing.Queue()
      loggingjob = multiprocessing.Process(target=log_listener,args=(logfilename,loggingqueue))
      loggingjob.start()

      try: 
         keystorefile = os.path.join(secbase,u'.pg-stat-profiler.keystr')
         profilesfile = os.path.join(secbase,u'.pg-stat-profiler.prof')
         keystore = api_keystore(apiconfig_secret,keystorefile)
         profile_store = profilestore(apiconfig_secret,profilesfile)
         collection_supervisor = collectorsupervisor(apiconfig_secret,profilesfile)
      except Exception as e:
         print('pg-stat-profiler: Failed to initiate data with secret, exiting... Reason [{}]'.format(str(e)))
         sys.exit()
      
      profilesqueue = multiprocessing.Queue()
      supervisorjob = multiprocessing.Process(target=collection_supervisor.run, args=(profilesqueue,loggingqueue))
      supervisorjob.start()
      scheduler.add_job(id=u'periodic_supervisorcheck',func=check_slavejobs,
            args=[loggingjob,loggingqueue,logfilename,supervisorjob,profilesqueue,collection_supervisor,profile_store],
            trigger='interval', seconds=10)      
      app.debug = False
      logging.warning('pg-stat-profiler: api started')
  except Exception as e:
      logging.warning("pg-stat-profiler: Unexpected Error ["+str(e)+"]")
      sys.exit()
  
  # main api Flask routing section
  # avoid blueprints for simplicity

  def fail_authenticate():
    return make_response(jsonify({'error': 'Not Authenticated'}), 401)

  def requires_keygensecret_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        request_auth = api_request(request)
        if not request_auth.getValid():
            return fail_authenticate()  
        else:
            requestkey = str(request_auth.getRequestkey()).lower().strip()
            if not apikeygen_secret == requestkey:
                return fail_authenticate()
        return f(*args, **kwargs)
    return decorated

  def requires_api_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        request_auth = api_request(request)
        if not request_auth.getValid():
            return fail_authenticate()  
        else:
            requestkey = str(request_auth.getRequestkey()).lower().strip()
            if not keystore.checkKey(requestkey):
                return fail_authenticate()
        return f(*args, **kwargs)
    return decorated


  @app.errorhandler(404)
  def not_found(error):
    return make_response(jsonify({"error": "Not Found"}), 404)

  @app.errorhandler(503)
  def not_supported(error):
    return make_response(jsonify({"error": "Not Supported"}), 503)

  @app.route('/_api/v1.0')
  @requires_api_auth
  def publish_welcome():
   try: 
    return jsonify({"postgres-stat-profiler":"welcome","version": "1.0.0"}) 
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 

  @app.route('/_api/v1.0/apikeys',methods=['GET'])
  @requires_keygensecret_auth
  def show_apikeys():
   try: 
    keystore.resetKeys();
    keys = []
    for i in range(0,5):
        keys.append(keystore.getApiKey(i))
    return make_response(jsonify({"apikeys": '{}'.format(str(keys))}),200)
   except Exception as e:
      return make_response(jsonify({"error": 'API Processing Error ('+str(e)+')'}),500) 
   
  @app.route('/_api/v1.0/profiles',methods=['GET'])
  @requires_api_auth
  def read_profiles():
   try: 
       if len(profile_store.getProfiles()) > 0:
          details = []
          for p in profile_store.getProfiles():
            details.append(profile_store.getApiDetails(p))
          return make_response(jsonify({"result" : details}),200)
       else:
         return make_response(jsonify({"error": "Not Found"}), 404)
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 

  @app.route('/_api/v1.0/profiles/<name>',methods=['GET'])
  @requires_api_auth
  def read_profile(name):
   try: 
    if profile_store.hasName(name):
       result = profile_store.getApiDetails(name)
       if 'name' in result:
          return make_response(jsonify({"result" : result}),200)
    return make_response(jsonify({"error": "Not Found"}), 404)
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 

  @app.route('/_api/v1.0/profiles/<name>',methods=['POST'])
  @requires_api_auth
  def create_profile(name):
   try:
    if profile_store.addProfileApi(name,request):
        return  make_response(jsonify({"result":"ok"}),200) 
    else:
        return make_response(jsonify({"result":"error"}),200) 
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 
   
  @app.route('/_api/v1.0/profiles/<name>/decryptQuery',methods=['POST'])
  @requires_api_auth
  def decrypt_profile_query(name):
   try:
    status,query = profile_store.decryptProfileQuery(name,request)
    if status:
        return  make_response(jsonify({"result":"ok","decrypted_query":query}),200) 
    else:
        return  make_response(jsonify({"result":"error"}),200) 
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 

  @app.route('/_api/v1.0/profiles/<name>',methods=['PUT'])
  @requires_api_auth
  def update_profile(name):
   try: 
    if profile_store.updateProfileApi(name,request):
        return  make_response(jsonify({"result":"ok"}),200) 
    else:
        return  make_response(jsonify({"result":"error"}),200) 
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 

  @app.route('/_api/v1.0/profiles/<name>',methods=['DELETE'])
  @requires_api_auth
  def delete_profile(name):
   try: 
    if profile_store.deleteProfileApi(name):
        return  make_response(jsonify({"result":"ok"}),200) 
    else:
        return  make_response(jsonify({"result":"error"}),200) 
   except Exception as e:
      return make_response(jsonify({"error": "API Processing Error ("+str(e)+")"}),500) 
  

  return app




 

# Helper functions