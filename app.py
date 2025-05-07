from flask import Flask, render_template, request, redirect, send_file, url_for
import sqlite3
from collections import defaultdict
import datetime
import routes.results
import routes.vote
import routes.scoreboard

from utils import add_votes, format_timedelta, get_show_id

app = Flask(__name__)

points = [20, 18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

@app.get('/')
def home():
    return render_template('home.html')

@app.get('/success')
def success():
    action = request.args.get('action')
    return render_template('successfully_voted.html', action=action)

@app.get('/favicon.ico')
def favicon():
    return send_file('files/favicon.ico')

@app.get('/error')
def error():
    error = request.args.get('error')
    return render_template('error.html', error=error)

app.add_url_rule('/vote', 'vote_index', routes.vote.vote_index, methods=['GET'])
app.add_url_rule('/vote/<show>', 'vote', routes.vote.vote, methods=['GET'])
app.add_url_rule('/vote/<show>', 'vote_post', routes.vote.vote_post, methods=['POST'])

app.add_url_rule('/results', 'results_index', routes.results.results_index, methods=['GET'])
app.add_url_rule('/results/<show>', 'results', routes.results.results, methods=['GET'])
app.add_url_rule('/results/<show>/detailed', 'detailed_results', routes.results.detailed_results, methods=['GET'])
app.add_url_rule('/results/<show>/scoreboard', 'scoreboard', routes.scoreboard.scoreboard, methods=['GET'])
app.add_url_rule('/results/<show>/scoreboard/votes', 'scoreboard_votes', routes.scoreboard.scores, methods=['GET'])

if __name__ == '__main__':
    app.run(debug=True, port=8000)