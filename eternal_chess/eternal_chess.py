#!/usr/bin/env python

import chess
import sqlite3
import eventlet
import threading
from random import randint
from chess.pgn import Game
from datetime import datetime
from contextlib import closing
from flask import Flask, g, render_template
from flask.ext.socketio import SocketIO


# Necessary because we use background threads.
eventlet.monkey_patch()

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

app = Flask(__name__)
app.config.from_envvar('ETERNAL_CHESS_CFG')

socketio = SocketIO(app)


def init_db():
    """Create the sqlite3 database using the configured schema file."""
    with closing(connect_db()) as db:
        with app.open_resource(app.config['SCHEMA_FILE'], mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()


def connect_db():
    """Return an established sqlite3 database connection."""
    return sqlite3.connect(app.config['DATABASE'])


def get_db():
    """Return or create an established sqlite3 database connection."""
    db = getattr(g, 'db', None)
    if not db:
        db = g.db = connect_db()
    return db


@app.before_request
def before_request():
    """Establish a database connection."""
    g.db = connect_db()


@app.teardown_request
def teardown_request(exception):
    """Close the established database connection."""
    db = getattr(g, 'db', None)
    if db:
        db.close()


def query_db(query, args=(), one=False):
    """Return the result of a database query."""
    with app.app_context():
        cur = get_db().execute(query, args)
        rs = cur.fetchall()
        cur.close()
        return (rs[0] if rs else None) if one else rs


def exec_db(query, args):
    """Execute an SQL statement on the configured database."""
    with app.app_context():
        get_db().execute(query, args)
        get_db().commit()


def insert_chess_game(completion_date, is_draw, n_moves, winner, pgn):
    """Add a row to chess_game."""
    query = ('INSERT INTO chess_game'
             '(completion_date, is_draw, n_moves, winner, pgn)'
             'VALUES (?, ?, ?, ?, ?)')
    args = [completion_date, is_draw, n_moves, winner, pgn]
    exec_db(query, args)


def get_n_of_games():
    """Return the number of completed games in the database."""
    with app.app_context():
        query = 'SELECT COUNT(*) FROM chess_game'
        result = query_db(query, one=True)[0]
        return int(result)


def get_n_white_wins():
    """Return the number of white chess game wins in the database."""
    with app.app_context():
        query = 'SELECT COUNT(*) FROM chess_game WHERE winner = "white"'
        result = query_db(query, one=True)[0]
        return int(result)


def get_n_black_wins():
    """Return the number of black chess game wins in the database."""
    with app.app_context():
        query = 'SELECT COUNT(*) FROM chess_game WHERE winner = "black"'
        result = query_db(query, one=True)[0]
        return int(result)


def get_n_draws():
    """Return the number of chess game draws in the database."""
    with app.app_context():
        query = 'SELECT COUNT(*) FROM chess_game WHERE is_draw = 1'
        result = query_db(query, one=True)[0]
        return int(result)


def get_total_moves():
    """Return the total number of chess moves made in the database."""
    with app.app_context():
        query = 'SELECT TOTAL(n_moves) FROM chess_game'
        result = query_db(query, one=True)[0]
        return int(result)


def play_chess(board):
    """Repeatedly play games of chess and record their results."""
    if board.is_game_over():
        record_result(board)
        socketio.emit('game_over', get_state())
        board.reset()
    moves = list(board.legal_moves)
    move = moves[randint(0, len(moves) - 1)]
    board.push(move)
    socketio.emit('move', {'fen': board.fen()})
    interval = app.config['MOVE_INTERVAL_SEC']
    threading.Timer(interval, lambda: play_chess(board)).start()


def record_result(board):
    """Add a chess game result to the database."""
    completion_date = datetime.now().strftime(DATE_FORMAT)
    is_draw = 1 if board.result() == '1/2-1/2' else 0
    n_moves = board.fullmove_number
    winner = 'white' if board.result() == '1-0' else 'black'
    if is_draw:
        winner = None
    pgn = configure_pgn(board)
    insert_chess_game(completion_date, is_draw, n_moves, winner, pgn)


def configure_pgn(board):
    """Return a PGN representation of a completed chess game."""
    pgn = Game.from_board(board)
    pgn.headers['Event'] = 'Eternal Chess'
    pgn.headers['Site'] = 'www.eternalchess.com'
    pgn.headers['Date'] = datetime.now().strftime(DATE_FORMAT)
    pgn.headers['Round'] = str(int(get_n_of_games()) + 1)
    pgn.headers['White'] = 'Random'
    pgn.headers['Black'] = 'Random'
    return str(pgn)


def get_state():
    """Return statistics for all chess games."""
    return {
        'n_games': get_n_of_games(),
        'n_white_wins': get_n_white_wins(),
        'n_black_wins': get_n_black_wins(),
        'n_draws': get_n_draws(),
        'n_moves': get_total_moves()
    }


@socketio.on('connect')
def test_connect():
    app.logger.info("Client connected.")


@socketio.on('disconnect')
def test_disconnect():
    app.logger.info("Client disconnected.")


@app.route('/')
def index():
    """Serve the root page."""
    return render_template('index.html', **get_state())


if __name__ == '__main__':
    play_chess(chess.Board())
    socketio.run(app)