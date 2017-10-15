"""
Redirector
"""
import json
from functools import wraps

from auth0.v3.authentication import GetToken, Users
from boxsdk.exception import BoxOAuthException
from flask import (Flask, jsonify, redirect, request, send_from_directory,
                   session)
import jwt
import requests

from biredirect import DB
from biredirect.boxstores import BoxKeysStoreRedis
from biredirect.settings import (AUTH0_CALLBACK_URL, AUTH0_CLIENT_ID,
                                 AUTH0_CLIENT_SECRET, AUTH0_DOMAIN,
                                 HEROKU_APP_NAME)

APP = Flask(__name__)
APP.secret_key = 'secret'  # TODO: Replace by real secret


# Login decorator
def jwt_required(scope):
    def real_decorator(function):
        @wraps(function)
        def wrapper(*orig_args, **orig_kwargs):
            # Return if testing
            if APP.config['TESTING']:
                return function(*orig_args, **orig_kwargs)
            # Get Bearer token
            access_token = request.headers.get('Authorization')
            if access_token:
                access_token = access_token.replace('Bearer ', '', 1)
                # Obtain first JWK and the keys to validate the signature
                jwks_object = requests.get(
                    f"https://{AUTH0_DOMAIN}/.well-known/jwks.json").json()
                jwks_key = jwt.algorithms.RSAAlgorithm.from_jwk(
                    json.dumps(jwks_object['keys'][0]))
                try:
                    payload = jwt.decode(
                        access_token, jwks_key, algorithms=['RS256'],
                        audience=f'https://{AUTH0_DOMAIN}/api/v2/',
                        issuer=f"https://{AUTH0_DOMAIN}/")
                    granted_scopes = payload['scope'].split()
                    if payload['sub'] and (scope in granted_scopes):
                        return function(*orig_args, **orig_kwargs)
                    return route_error(401, "Unauthorized user")
                except jwt.ExpiredSignatureError:
                    return route_error(401, "Expired Token")
            return route_error(401, "Authorization needed")
        return wrapper
    if callable(scope):
        return real_decorator(scope)
    return real_decorator


def route_error(code, message):
    print('Error!:', code, message)
    response = jsonify({'error': message})
    response.status_code = code
    return response


# API Routes
@APP.route('/api/redirect', methods=['GET'])
def redirect_to_box():
    """
    Redirect to the Box download address
    """
    doc_id = request.args.get('docID')
    if doc_id is None:
        return 'a'

    new_url = f"https://amadeus.box.com/shared/static/{doc_id}"
    return redirect(new_url)


@APP.route('/api/configs', methods=['POST'])
@jwt_required(scope='create:config')
def create_config():
    try:
        data = request.json
        data['name'] = data['name'].upper()
        if DB.config_exists(data['name']):
            return jsonify({"error": "Config already exists"}), 404
        config = DB.insert_config(data)
        if config:
            return jsonify({'data': config}), 201
        return jsonify({"error": "Config don't created"}), 404
    except ValueError:
        return jsonify({"error": "Config should have a name"}), 404


@APP.route('/api/configs', methods=['GET'])
@jwt_required(scope='read:config')
def retrieve_configs():
    """
    Return all configs
    """
    return jsonify({'data': DB.get_configs()})


@APP.route('/api/configs/<string:config_name>', methods=['GET'])
@jwt_required(scope='read:config')
def retrieve_config(config_name):
    config = DB.get_config(config_name)
    if config:
        return jsonify({'data': config})
    return jsonify({"error": "name doesn't exist"}), 404


@APP.route('/api/configs/<string:config_name>', methods=['PUT'])
@jwt_required(scope='update:config')
def update_config(config_name):
    config = DB.update_config(config_name, request.json)
    if config:
        return jsonify({'data': config})
    return jsonify({'error': 'Not updated'})


@APP.route('/api/configs/<string:config_name>', methods=['DELETE'])
@jwt_required(scope='delete:config')
def delete_config(config_name):
    if DB.delete_config(config_name):
        return jsonify({'result': 'success'})
    return jsonify({'error': 'Not deleted'}), 404


# Internal Logins
@APP.route('/api/authcallback')
def callback_handling():
    error = request.args.get('error')
    if error is None:
        code = request.args.get('code')
        redirect_url = request.args.get('redirectto')
        if redirect_url is None:
            redirect_url = '/'
        get_token = GetToken(AUTH0_DOMAIN)
        auth0_users = Users(AUTH0_DOMAIN)
        token = get_token.authorization_code(AUTH0_CLIENT_ID,
                                             AUTH0_CLIENT_SECRET,
                                             code, AUTH0_CALLBACK_URL)
        user_info = auth0_users.userinfo(token['access_token'])
        session['profile'] = json.loads(user_info)
        return redirect(redirect_url)
    else:
        error_msg = request.args.get('error_description')
        return f'<h1>{error}</h1><p>{error_msg}<p>', 401


@APP.route('/logout')
@jwt_required
def logout():
    session.clear()
    base_url = f'https://{HEROKU_APP_NAME}.herokuapp.com/'
    return redirect(
        f'https://{AUTH0_DOMAIN}/v2/logout?'
        f'returnTo={base_url}&client_id={AUTH0_CLIENT_ID}')


# Box logins
@APP.route("/api/box/authenticate")
def authenticate():
    """
    Launches the Box authentication process
    """
    box_key_store = BoxKeysStoreRedis()
    oauth = box_key_store.get_oauth()
    if HEROKU_APP_NAME:
        redirect_url = f'https://{HEROKU_APP_NAME}.herokuapp.com/api/box/callback'
    else:
        redirect_url = f'http://localhost:5000/api/box/callback'
    auth_url, csrf_token = oauth.get_authorization_url(redirect_url)
    DB.set_crsf_token(csrf_token)
    # Redirect to Box Oauth
    return redirect(auth_url)


@APP.route("/api/box/callback")
def boxauth():
    """
    Stores the received tokens in oauth.
    """
    csrf_token = request.args.get('state')
    auth_token = request.args.get('code')
    try:
        assert DB.get_crsf_token() == csrf_token
        box_key_store = BoxKeysStoreRedis()
        box_key_store.get_oauth().authenticate(auth_token)
        response = "Authenticated. You can close this window."
    except BoxOAuthException as ex:  # pragma: no cover
        response = ex
    except AssertionError:
        response = "Tokens don't match"
    return response

# Fileserver


# Catch All urls, enabling copy-paste url
@APP.route('/', defaults={'location': ''})
@APP.route('/<path:location>')  # Catch All urls, enabling copy-paste url
def home(location):
    print('here: %s' % location)
    if location == '' or '.' not in location:
        location = 'index.html'
    return send_from_directory('./dist', location)
