import sys
import os
import logging
from flask import Flask, abort, jsonify, make_response, request, Response
from flask_apscheduler import APScheduler
from functools import wraps
import multiprocessing
from postgres_stat_profiler.api_auth.api_auth import api_auth
from postgres_stat_profiler.api_auth.api_keystore import api_keystore
from postgres_stat_profiler.config.profilestore import profilestore
from postgres_stat_profiler.supervision.profilesupervisor import profilesupervisor

# environment 
os.environ['PG_STAT_PROFILER_SECRET'] = '4958034759875304895734897543875403985740987540785078438859074'
os.environ['PG_STAT_PROFILER_BASE'] = '/Users/y7kwh/Documents/GitHub/postgres_stat_profiler/postgres_stat_profiler'
os.environ['PG_STAT_PROFILER_LOGBASE'] = '/Users/y7kwh/Documents/GitHub/postgres_stat_profiler/postgres_stat_profiler/resources/log'
logfilename = os.path.join(os.getenv(u'PG_STAT_PROFILER_LOGBASE'),u"pg-stat-profiler.log")
keystorefile = os.path.join(os.getenv(u'PG_STAT_PROFILER_BASE'),u'resources/sec/.pg-stat-profiler.keystr')
profilesfile = os.path.join(os.getenv(u'PG_STAT_PROFILER_BASE'),u'resources/sec/.pg-stat-profiler.prof')

# logging
logging.basicConfig(filename = logfilename, level=logging.WARNING,
                    format='%(asctime)s[%(funcName)-5s] (%(processName)-10s) %(message)s',
                    )
logging.warning("Startup : postgres-stat-profiler")

# store (state) initialisation
api_secret = os.getenv(u'PG_STAT_PROFILER_SECRET')
if api_secret:  
      keystore = api_keystore(api_secret,keystorefile)
      profile_store = profilestore(api_secret,profilesfile)
      profile_supervisor = profilesupervisor(api_secret,profilesfile)
else:
      logging.warning("Exception Shutdown : postgres-stat-profiler: No secret supplied")
      sys.exit()


# profile supervisor control function: runs periodically in parallel with Flask

def check_supervisorjob():
  global supervisorjob
  global profilesqueue
  global profile_supervisor
  global profile_store
  try:
   # 
   if not supervisorjob.is_alive():
      supervisorjob.join()
      supervisorjob = multiprocessing.Process(target=profile_supervisor.run,args=(profilesqueue,))
      supervisorjob.start()
      logging.warning("Postgres Stat Profiler: supervisor restarted with processid :[{}]".format(str(supervisorjob.pid)))
   else:
     #  handle profile state changes passed from individual profiles up via supervisor
     #  ensures that only this main thread updates the persistent profilesfilestore
     while not profilesqueue.empty(): 
        qdata = profilesqueue.get()
        if 'name' in qdata:
           profile_store.updateProfile(qdata['name'],qdata)
  except Exception as e:
      logging.warning("Unexpected Exception : Postgres Stat Profiler: Error checking supervisor :[{}]".format(str(e)))

# main api Flask section

app = Flask(__name__)
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

def fail_authenticate():
    return make_response(jsonify({'error': 'Not Authenticated'}), 401)

def requires_secret_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        request_auth = api_auth(request)
        if not request_auth.getValid():
            return fail_authenticate()  
        else:
            requestkey = str(request_auth.getRequestkey()).lower().strip()
            if not api_secret == requestkey:
                return fail_authenticate()
        return f(*args, **kwargs)
    return decorated

def requires_api_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        request_auth = api_auth(request)
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
    return make_response(jsonify({'error': 'Not Found'}), 404)

@app.errorhandler(503)
def not_supported(error):
    return make_response(jsonify({'error': 'Not Supported'}), 503)

@app.route('/_api/v1.0')
@requires_api_auth
def publish_welcome():
   try: 
    return jsonify({'postgres-stat-profiler':'welcome','version': '1.0.0'}) 
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

@app.route('/_api/v1.0/apikeys')
@requires_secret_auth
def show_apikeys():
   try: 
    keys = []
    for i in range(0,5):
        keys.append(keystore.getApiKey(i))
    return make_response(jsonify({'apikeys': '{}'.format(str(keys))}),200)
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 
   
@app.route('/_api/v1.0/profiles',methods=['GET'])
@requires_api_auth
def read_profiles():
   try: 
       if len(profile_store.getProfiles()) > 0:
          details = []
          for p in profile_store.getProfiles():
            details.append(profile_store.getApiDetails(p))
          return jsonify(details,200)
       else:
         return make_response(jsonify({'error': 'Not Found'}), 404)
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

@app.route('/_api/v1.0/profiles/<name>',methods=['GET'])
@requires_api_auth
def read_profile(name):
   try: 
    if profile_store.hasName(name):
       result = profile_store.getApiDetails(name)
       if 'name' in result:
          return jsonify(result,200)
    return make_response(jsonify({'error': 'Not Found'}), 404)
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

@app.route('/_api/v1.0/profiles/<name>',methods=['POST'])
@requires_api_auth
def create_profile(name):
   try:
    if profile_store.addProfileApi(name,request):
        return jsonify({'result':'ok'},200) 
    else:
        return jsonify({'result':'error'},200) 
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

@app.route('/_api/v1.0/profiles/<name>',methods=['PUT'])
@requires_api_auth
def update_profile(name):
   try: 
    if profile_store.updateProfileApi(name,request):
        return jsonify({'result':'ok'},200) 
    else:
        return jsonify({'result':'error'},200) 
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

@app.route('/_api/v1.0/profiles/<name>',methods=['DELETE'])
@requires_api_auth
def delete_profile(name):
   try: 
    if profile_store.deleteProfileApi(name):
        return jsonify({'result':'ok'},200) 
    else:
        return jsonify({'result':'error'},200) 
   except Exception as e:
      return make_response(jsonify({'error': 'API Processing Error ('+str(e)+')'}),500) 

def main():
  try:
      profilesqueue = multiprocessing.Queue()
      supervisorjob = multiprocessing.Process(target=profile_supervisor.run, args=(profilesqueue,))
      supervisorjob.start()
      scheduler.add_job(id=u'periodic_supervisorcheck',func=check_supervisorjob, trigger='interval', seconds=10)      
      app.debug = False
      app.run(ssl_context='adhoc')
  except Exception as e:
      logging.warning("Exception Shutdown : postgres-stat-profiler: Error ["+str(e)+"]")
      sys.exit()

if __name__ == '__main__':
   main()

# Helper functions

