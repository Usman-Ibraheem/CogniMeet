"""
Voice Study Chat - Fixed Flask Application with Daily.co
Install dependencies: pip install flask flask-socketio flask-login flask-sqlalchemy requests python-dotenv
Run: python app.py
"""

from flask import Flask, render_template_string, session, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import secrets
import hashlib
import time
import os
import requests
from collections import deque

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///voice_chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Daily.co Configuration
DAILY_API_KEY = os.environ.get('DAILY_API_KEY', '9789ec46ecd5fff641d8395bb0304aeced23e5b92853493fcc4985295553c882')  # Set this in environment or here
DAILY_DOMAIN = os.environ.get('DAILY_DOMAIN', 'growthstudent')  # Your Daily.co domain

# In-memory queue for matching
waiting_queue = deque()
active_sessions = {}

# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class VoiceSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    room_name = db.Column(db.String(100), nullable=False)
    room_url = db.Column(db.String(200), nullable=False)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_ai_session = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)

# ============================================================================
# DAILY.CO ROOM MANAGEMENT - FIXED VERSION
# ============================================================================

def create_daily_room(room_name):
    """Create a Daily.co room with proper audio configuration"""
    
    # If no API key, use public Daily.co demo (limited but works)
    if not DAILY_API_KEY:
        print("⚠️  No Daily API key found. Using demo room (may have limitations)")
        demo_url = f"https://{DAILY_DOMAIN}.daily.co/{room_name}"
        return demo_url, room_name
    
    headers = {
        'Authorization': f'Bearer {DAILY_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    data = {
        'name': room_name,
        'privacy': 'public',  # Important: public rooms work without authentication
        'properties': {
            'enable_chat': False,
            'enable_screenshare': False,
            'enable_recording': False,
            'start_video_off': True,
            'start_audio_off': False,  # Audio should be ON by default
            'enable_prejoin_ui': False,
            'enable_network_ui': True,  # Enable for debugging
            'enable_people_ui': True,   # Enable for debugging
            'exp': int(time.time()) + 7200,  # 2 hour expiry
            'eject_at_room_exp': True,
            'enable_knocking': False,
            'max_participants': 10
        }
    }
    
    try:
        response = requests.post(
            'https://api.daily.co/v1/rooms',
            headers=headers,
            json=data,
            timeout=10
        )
        
        if response.status_code == 200:
            room_data = response.json()
            print(f"✓ Created Daily.co room: {room_data['url']}")
            return room_data['url'], room_data['name']
        else:
            print(f"⚠️  API error {response.status_code}: {response.text}")
            # Fallback to demo room
            fallback_url = f"https://{DAILY_DOMAIN}.daily.co/{room_name}"
            print(f"⚠️  Using fallback URL: {fallback_url}")
            return fallback_url, room_name
            
    except Exception as e:
        print(f"⚠️  Error creating Daily room: {e}")
        fallback_url = f"https://{DAILY_DOMAIN}.daily.co/{room_name}"
        print(f"⚠️  Using fallback URL: {fallback_url}")
        return fallback_url, room_name

def delete_daily_room(room_name):
    """Delete a Daily.co room"""
    if not DAILY_API_KEY:
        return True  # Can't delete demo rooms
        
    headers = {
        'Authorization': f'Bearer {DAILY_API_KEY}'
    }
    
    try:
        response = requests.delete(
            f'https://api.daily.co/v1/rooms/{room_name}',
            headers=headers,
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Error deleting Daily room: {e}")
        return False

# ============================================================================
# AUTHENTICATION
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400
        
        user = User.query.filter_by(username=username).first()
        
        if not user:
            # Create new user
            hashed_pw = hashlib.sha256(password.encode()).hexdigest()
            user = User(username=username, password=hashed_pw)
            db.session.add(user)
            db.session.commit()
        else:
            # Verify password
            hashed_pw = hashlib.sha256(password.encode()).hexdigest()
            if user.password != hashed_pw:
                return jsonify({'error': 'Invalid password'}), 401
        
        login_user(user)
        return jsonify({'success': True, 'redirect': url_for('index')})
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
@login_required
def logout():
    global waiting_queue
    waiting_queue = deque([u for u in waiting_queue if u['user_id'] != current_user.id])
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template_string(CHAT_TEMPLATE, username=current_user.username)

# ============================================================================
# SOCKETIO EVENTS - Real-time matching
# ============================================================================

@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False
    print(f'✓ User {current_user.username} connected')
    emit('connected', {'user_id': current_user.id})

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        print(f'✗ User {current_user.username} disconnected')
        global waiting_queue
        waiting_queue = deque([u for u in waiting_queue if u['user_id'] != current_user.id])

@socketio.on('join_queue')
def handle_join_queue():
    """User wants to find a partner"""
    if not current_user.is_authenticated:
        return
    
    user_id = current_user.id
    username = current_user.username
    
    # Remove from queue if already there
    global waiting_queue
    waiting_queue = deque([u for u in waiting_queue if u['user_id'] != user_id])
    
    # Try to match with someone in queue
    if len(waiting_queue) > 0:
        partner = waiting_queue.popleft()
        
        # Create session with simpler room name
        session_id = secrets.token_urlsafe(16)
        room_name = f"room{secrets.token_hex(8)}"  # Simpler room name
        
        # Create Daily.co room
        room_url, room_name = create_daily_room(room_name)
        
        session_obj = VoiceSession(
            session_id=session_id,
            room_name=room_name,
            room_url=room_url,
            user1_id=partner['user_id'],
            user2_id=user_id,
            is_ai_session=False
        )
        db.session.add(session_obj)
        db.session.commit()
        
        # Store in active sessions
        active_sessions[session_id] = {
            'user1_id': partner['user_id'],
            'user2_id': user_id,
            'room_name': room_name,
            'room_url': room_url,
            'created_at': time.time()
        }
        
        print(f'✓ Matched {username} with {partner["username"]} in room: {room_url}')
        
        # Notify both users
        socketio.emit('matched', {
            'session_id': session_id,
            'room_url': room_url,
            'is_ai': False,
            'partner_name': username
        }, room=partner['sid'])
        
        emit('matched', {
            'session_id': session_id,
            'room_url': room_url,
            'is_ai': False,
            'partner_name': partner['username']
        })
        
    else:
        # Add to queue
        waiting_queue.append({
            'user_id': user_id,
            'username': username,
            'sid': request.sid,
            'joined_at': time.time()
        })
        emit('waiting', {'position': len(waiting_queue)})
        print(f'⏳ {username} added to queue, position: {len(waiting_queue)}')
        
        # Check for timeout after 8 seconds
        socketio.start_background_task(check_timeout, user_id, request.sid)

def check_timeout(user_id, sid):
    """Check if user has been waiting too long"""
    time.sleep(8)
    
    global waiting_queue
    user_in_queue = any(u['user_id'] == user_id for u in waiting_queue)
    
    if user_in_queue:
        # Create AI session
        waiting_queue = deque([u for u in waiting_queue if u['user_id'] != user_id])
        
        session_id = secrets.token_urlsafe(16)
        room_name = f"ai{secrets.token_hex(8)}"
        
        # Create Daily.co room
        room_url, room_name = create_daily_room(room_name)
        
        session_obj = VoiceSession(
            session_id=session_id,
            room_name=room_name,
            room_url=room_url,
            user1_id=user_id,
            is_ai_session=True
        )
        db.session.add(session_obj)
        db.session.commit()
        
        active_sessions[session_id] = {
            'user1_id': user_id,
            'user2_id': None,
            'room_name': room_name,
            'room_url': room_url,
            'created_at': time.time(),
            'is_ai': True
        }
        
        socketio.emit('matched', {
            'session_id': session_id,
            'room_url': room_url,
            'is_ai': True
        }, room=sid)
        
        print(f'🤖 User {user_id} matched with AI after timeout')

@socketio.on('cancel_search')
def handle_cancel_search():
    """User cancels search"""
    if not current_user.is_authenticated:
        return
    
    global waiting_queue
    waiting_queue = deque([u for u in waiting_queue if u['user_id'] != current_user.id])
    emit('search_cancelled')
    print(f'✗ User {current_user.username} cancelled search')

@socketio.on('end_session')
def handle_end_session(data):
    """End current session"""
    if not current_user.is_authenticated:
        return
    
    session_id = data.get('session_id')
    if session_id:
        session_obj = VoiceSession.query.filter_by(session_id=session_id).first()
        if session_obj:
            session_obj.status = 'ended'
            session_obj.ended_at = datetime.utcnow()
            db.session.commit()
            
            # Delete Daily.co room
            delete_daily_room(session_obj.room_name)
            
            if session_id in active_sessions:
                del active_sessions[session_id]
    
    emit('session_ended')

# ============================================================================
# HTML TEMPLATES
# ============================================================================

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Voice Study Chat</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 400px;
            padding: 40px;
        }
        .logo { font-size: 64px; text-align: center; margin-bottom: 20px; }
        h1 { color: #333; font-size: 28px; text-align: center; margin-bottom: 10px; }
        .subtitle { color: #666; text-align: center; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #333; font-weight: 600; margin-bottom: 8px; font-size: 14px; }
        input {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.3s;
        }
        button:hover { transform: translateY(-2px); }
        .error { color: #ff4757; text-align: center; margin-top: 15px; font-size: 14px; }
        .info { color: #666; text-align: center; margin-top: 15px; font-size: 13px; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">🎓</div>
        <h1>Voice Study Chat</h1>
        <p class="subtitle">Connect with students for voice discussions</p>
        
        <form id="loginForm">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit">Login / Register</button>
            <p class="info">New users will be automatically registered</p>
            <div id="error" class="error"></div>
        </form>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const errorDiv = document.getElementById('error');
            errorDiv.textContent = '';
            
            const formData = {
                username: document.getElementById('username').value,
                password: document.getElementById('password').value
            };
            
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                
                const data = await response.json();
                
                if (data.success) {
                    window.location.href = data.redirect;
                } else {
                    errorDiv.textContent = data.error || 'Login failed';
                }
            } catch (error) {
                errorDiv.textContent = 'Network error. Please try again.';
            }
        });
    </script>
</body>
</html>
'''

CHAT_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Voice Study Chat</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script crossorigin src="https://unpkg.com/@daily-co/daily-js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .chat-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 500px;
            padding: 40px;
            text-align: center;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .username { font-size: 14px; color: #666; }
        .logout-btn {
            padding: 8px 16px;
            background: #f0f0f0;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
        }
        .logo { font-size: 48px; margin-bottom: 10px; }
        h1 { color: #333; font-size: 28px; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .status-icon {
            font-size: 80px;
            margin-bottom: 20px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.1); opacity: 0.8; }
        }
        .status-text { font-size: 20px; color: #333; font-weight: 600; margin-bottom: 10px; }
        .status-subtext { color: #666; font-size: 14px; margin-bottom: 5px; }
        .debug-info {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 10px;
            margin-top: 10px;
            font-size: 12px;
            color: #666;
            text-align: left;
            max-height: 150px;
            overflow-y: auto;
        }
        button {
            padding: 16px 24px;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            margin-top: 20px;
        }
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .btn-secondary { background: #f0f0f0; color: #333; }
        .btn-danger { background: #ff4757; color: white; }
        .controls {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin: 20px 0;
        }
        .control-btn {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            margin: 0;
        }
        .hidden { display: none; }
        .timer { font-size: 14px; color: #666; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="header">
            <div class="username">👋 {{ username }}</div>
            <a href="/logout"><button class="logout-btn">Logout</button></a>
        </div>
        
        <div class="logo">🎓</div>
        <h1>Voice Study Chat</h1>
        <p class="subtitle">Connect instantly with fellow students</p>
        
        <div id="initial-state">
            <div class="status-icon">🎤</div>
            <div class="status-text">Ready to Connect</div>
            <div class="status-subtext">Click below to find a study partner</div>
            <button class="btn-primary" id="connect-btn">Connect Now</button>
        </div>
        
        <div id="searching-state" class="hidden">
            <div class="status-icon">🔍</div>
            <div class="status-text">Searching for partner...</div>
            <div class="status-subtext">Finding someone to connect with</div>
            <button class="btn-secondary" id="cancel-btn">Cancel</button>
        </div>
        
        <div id="connected-state" class="hidden">
            <div class="status-icon">👥</div>
            <div class="status-text">Connected</div>
            <div class="status-subtext" id="connection-type">Voice chat active</div>
            <div class="timer" id="call-timer">00:00</div>
            
            <div class="controls">
                <button class="control-btn btn-secondary" id="mute-btn">🎤</button>
                <button class="control-btn btn-danger" id="end-call-btn">📞</button>
            </div>
            
            <button class="btn-secondary" id="next-btn">Next Partner</button>
            
            <div class="debug-info" id="debug-info">
                <strong>Connection Debug:</strong><br>
                <span id="debug-text">Initializing...</span>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        
        let callObject = null;
        let currentSession = null;
        let isMuted = false;
        let callStartTime = null;
        let timerInterval = null;
        
        function debugLog(message) {
            console.log(message);
            const debugText = document.getElementById('debug-text');
            if (debugText) {
                const timestamp = new Date().toLocaleTimeString();
                debugText.innerHTML += `<br>[${timestamp}] ${message}`;
                debugText.parentElement.scrollTop = debugText.parentElement.scrollHeight;
            }
        }
        
        // Socket events
        socket.on('connect', () => {
            console.log('Connected to server');
            debugLog('✓ Connected to server');
        });
        
        socket.on('waiting', (data) => {
            console.log('Waiting in queue, position:', data.position);
            debugLog(`⏳ Waiting in queue, position: ${data.position}`);
        });
        
        socket.on('matched', async (data) => {
            console.log('Matched!', data);
            debugLog(`✓ Matched! Room: ${data.room_url}`);
            currentSession = data;
            
            let typeText = data.is_ai ? 'Connected to AI Assistant' : `Connected to: ${data.partner_name || 'a student'}`;
            document.getElementById('connection-type').textContent = typeText;
            
            showState('connected');
            startCallTimer();
            
            // Join call with delay to ensure UI is ready
            setTimeout(async () => {
                await joinDailyCall(data.room_url);
            }, 500);
        });
        
        socket.on('error', (data) => {
            alert(data.message);
            debugLog(`❌ Error: ${data.message}`);
            showState('initial');
        });
        
        socket.on('session_ended', () => {
            cleanup();
            showState('initial');
        });
        
        // Button events
        document.getElementById('connect-btn').addEventListener('click', () => {
            socket.emit('join_queue');
            showState('searching');
        });
        
        document.getElementById('cancel-btn').addEventListener('click', () => {
            socket.emit('cancel_search');
            showState('initial');
        });
        
        document.getElementById('mute-btn').addEventListener('click', () => {
            if (!callObject) return;
            isMuted = !isMuted;
            callObject.setLocalAudio(!isMuted);
            document.getElementById('mute-btn').textContent = isMuted ? '🔇' : '🎤';
            debugLog(isMuted ? '🔇 Muted' : '🎤 Unmuted');
        });
        
        document.getElementById('end-call-btn').addEventListener('click', async () => {
            if (currentSession) {
                socket.emit('end_session', { session_id: currentSession.session_id });
            }
            await cleanup();
            showState('initial');
        });
        
        document.getElementById('next-btn').addEventListener('click', async () => {
            if (currentSession) {
                socket.emit('end_session', { session_id: currentSession.session_id });
            }
            await cleanup();
            socket.emit('join_queue');
            showState('searching');
        });
        
        // Daily.co functions - FIXED VERSION
        async function joinDailyCall(roomUrl) {
            try {
                debugLog(`📞 Joining call: ${roomUrl}`);
                
                // Request microphone permission first
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    debugLog('✓ Microphone permission granted');
                    stream.getTracks().forEach(track => track.stop());
                } catch (err) {
                    debugLog(`❌ Microphone permission denied: ${err.message}`);
                    alert('Please allow microphone access to use voice chat.');
                    return;
                }
                
                // Create call object
                callObject = window.DailyIframe.createCallObject({
                    audioSource: true,
                    videoSource: false
                });
                
                // Event listeners
                callObject.on('joined-meeting', (event) => {
                    console.log('✓ Joined meeting:', event);
                    debugLog('✓ Successfully joined meeting');
                    debugLog(`👤 Your ID: ${event.participants.local.user_id}`);
                });
                
                callObject.on('participant-joined', (event) => {
                    console.log('✓ Participant joined:', event.participant);
                    debugLog(`👥 Partner joined: ${event.participant.user_name || 'Anonymous'}`);
                });
                
                callObject.on('participant-left', (event) => {
                    console.log('Participant left:', event.participant);
                    debugLog(`👋 Partner left`);
                });
                
                callObject.on('track-started', (event) => {
                    console.log('✓ Track started:', event);
                    if (event.participant && event.participant.local) {
                        debugLog(`🎤 Your audio track started`);
                    } else {
                        debugLog(`🔊 Partner's audio track started`);
                    }
                });
                
                callObject.on('track-stopped', (event) => {
                    console.log('Track stopped:', event);
                    debugLog(`⏹️ Audio track stopped`);
                });
                
                callObject.on('error', (error) => {
                    console.error('Daily.co error:', error);
                    debugLog(`❌ Call error: ${error.errorMsg || error.message}`);
                });
                
                callObject.on('left-meeting', (event) => {
                    debugLog('👋 Left meeting');
                });
                
                // Join the call
                debugLog('🔗 Connecting to room...');
                await callObject.join({ 
                    url: roomUrl,
                    userName: '{{ username }}',
                    startAudioOff: false,
                    startVideoOff: true
                });
                
                debugLog('✓ Call object created and joined');
                
                // Ensure audio is on after a short delay
                setTimeout(() => {
                    if (callObject) {
                        callObject.setLocalAudio(true);
                        const participants = callObject.participants();
                        debugLog(`📊 Total participants: ${Object.keys(participants).length}`);
                        debugLog('✓ Audio explicitly enabled');
                    }
                }, 1000);
                
            } catch (error) {
                console.error('Failed to join Daily.co call:', error);
                debugLog(`❌ Failed to join: ${error.message}`);
                
                if (error.errorMsg && error.errorMsg.includes('permission')) {
                    alert('Please allow microphone access to use voice chat.');
                } else {
                    alert('Failed to connect to voice chat. Please try again.');
                }
            }
        }
        
        async function cleanup() {
            debugLog('🧹 Cleaning up call...');
            if (timerInterval) clearInterval(timerInterval);
            if (callObject) {
                try {
                    await callObject.leave();
                    await callObject.destroy();
                    debugLog('✓ Call ended and cleaned up');
                } catch (e) {
                    console.error('Error during cleanup:', e);
                    debugLog(`⚠️ Cleanup error: ${e.message}`);
                }
                callObject = null;
            }
            currentSession = null;
            isMuted = false;
            
            // Reset debug info
            const debugText = document.getElementById('debug-text');
            if (debugText) {
                debugText.innerHTML = 'Initializing...';
            }
        }
        
        function startCallTimer() {
            callStartTime = Date.now();
            timerInterval = setInterval(() => {
                const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
                const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
                const seconds = (elapsed % 60).toString().padStart(2, '0');
                document.getElementById('call-timer').textContent = `${minutes}:${seconds}`;
            }, 1000);
        }
        
        function showState(state) {
            document.getElementById('initial-state').classList.add('hidden');
            document.getElementById('searching-state').classList.add('hidden');
            document.getElementById('connected-state').classList.add('hidden');
            document.getElementById(state + '-state').classList.remove('hidden');
        }
        
        window.addEventListener('beforeunload', () => {
            if (currentSession) {
                socket.emit('end_session', { session_id: currentSession.session_id });
            }
        });
    </script>
</body>
</html>
'''

# ============================================================================
# DATABASE INITIALIZATION & MIGRATION
# ============================================================================

def migrate_database():
    """Migrate database from Agora to Daily.co schema"""
    with app.app_context():
        inspector = db.inspect(db.engine)
        
        # Check if voice_session table exists
        if 'voice_session' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('voice_session')]
            
            # If old schema detected (has channel_name but not room_name)
            if 'channel_name' in columns and 'room_name' not in columns:
                print("⚠️  Old Agora schema detected. Migrating to Daily.co schema...")
                
                # Drop the old table
                VoiceSession.__table__.drop(db.engine)
                print("✓ Dropped old voice_session table")
        
        # Create all tables with new schema
        db.create_all()
        print("✓ Database initialized with Daily.co schema!")

migrate_database()

# ============================================================================
# RUN APPLICATION
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("Voice Study Chat Server Starting...")
    print("=" * 60)
    print("Daily.co Integration Enabled")
    if DAILY_API_KEY:
        print("✓ Using Daily.co API key")
    else:
        print("⚠️  No API key - using demo rooms (limited features)")
    print(f"Domain: {DAILY_DOMAIN}.daily.co")
    print("Open your browser to: http://localhost:5000")
    print("=" * 60)
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)