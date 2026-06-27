from flask import Flask, jsonify, render_template
import requests
import os
import time

app = Flask(__name__)

# Fetch the API key from Render's Environment Variables
API_KEY = os.environ.get('FOOTBALL_DATA_API_KEY')
BASE_URL = 'https://api.football-data.org/v4/competitions/WC'

# Simple in-memory cache to prevent hitting the 10 requests/min rate limit
cache = {
    'matches': {'data': None, 'timestamp': 0},
    'standings': {'data': None, 'timestamp': 0}
}
CACHE_TTL = 300  # 5 minutes in seconds

def fetch_from_api(endpoint):
    """Helper function to fetch data from football-data.org"""
    if not API_KEY:
        raise ValueError("API Key is missing. Please set FOOTBALL_DATA_API_KEY in Render.")
        
    headers = {'X-Auth-Token': API_KEY}
    response = requests.get(f"{BASE_URL}/{endpoint}", headers=headers)
    response.raise_for_status()
    return response.json()

@app.route('/')
def index():
    """Serve the main frontend HTML page"""
    return render_template('index.html')

@app.route('/api/matches')
def get_matches():
    """Endpoint for the frontend to fetch matches (cached)"""
    now = time.time()
    
    # Return cached data if it's less than 5 minutes old
    if cache['matches']['data'] and (now - cache['matches']['timestamp'] < CACHE_TTL):
        return jsonify(cache['matches']['data'])
    
    try:
        data = fetch_from_api('matches')
        cache['matches'] = {'data': data, 'timestamp': now}
        return jsonify(data)
    except Exception as e:
        print(f"Error fetching matches: {e}")
        return jsonify({'error': 'Failed to fetch matches data'}), 500

@app.route('/api/standings')
def get_standings():
    """Endpoint for the frontend to fetch standings (cached)"""
    now = time.time()
    
    if cache['standings']['data'] and (now - cache['standings']['timestamp'] < CACHE_TTL):
        return jsonify(cache['standings']['data'])
    
    try:
        data = fetch_from_api('standings')
        cache['standings'] = {'data': data, 'timestamp': now}
        return jsonify(data)
    except Exception as e:
        print(f"Error fetching standings: {e}")
        return jsonify({'error': 'Failed to fetch standings data'}), 500

if __name__ == '__main__':
    # When run locally, this will use port 5000. 
    # On Render, it uses the PORT environment variable.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)