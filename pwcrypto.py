# Fix for Python 3.13 (Render removing imghdr)
import sys, types
if sys.version_info >= (3, 13):
    sys.modules["imghdr"] = types.ModuleType("imghdr")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import re, random, string, os, json, html
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.users import GetFullUserRequest
import asyncio
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# ==========================
# CONFIGURATION
#  ==========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
DATABASE_URL = os.getenv("DATABASE_URL")

# Telethon client for bio checking
telethon_client = None

# Database connection pool
db_pool = None

# Bio check cache: {user_id: {"has_pagal": bool, "fetched_at": timestamp}}
bio_cache = {}
bio_cache_lock = None
BIO_CACHE_TTL = int(os.getenv("BIO_CACHE_TTL", "21600"))  # 6 hours default

async def initialize_telethon():
    """Initialize and login to Telethon for bio checking."""
    global telethon_client, bio_cache_lock
    
    # Initialize cache lock
    bio_cache_lock = asyncio.Lock()
    
    if not API_ID or not API_HASH:
        print("⚠️ Telethon credentials not configured - bio checking disabled")
        return False
    
    # Check if session file exists
    if not os.path.exists("bio_session.session"):
        print("⚠️ Session file not found - bio checking disabled")
        print("ℹ️  To enable bio detection, run LOCAL_AUTH_TELETHON.py on your computer and upload bio_session.session")
        return False
    
    try:
        print("🔄 Initializing Telethon client...")
        telethon_client = TelegramClient(
            "bio_session",
            int(API_ID),
            API_HASH
        )
        
        # Connect and check if authorized
        await telethon_client.connect()
        
        if not await telethon_client.is_user_authorized():
            print("❌ Session is not authorized")
            return False
        
        me = await telethon_client.get_me()
        print(f"✅ Telethon logged in as: {me.first_name} (@{me.username if me.username else 'no_username'})")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize Telethon: {e}")
        print("⚠️ Session may be corrupted. Please regenerate using LOCAL_AUTH_TELETHON.py")
        telethon_client = None
        return False

# Admins (numeric Telegram user IDs)
ADMINS = [5229586098, 7962772947, 7880967664, 910096684, 5825027777, 6864194951, 6999316166, 2001575810, 7422906767]  # @A812sss, @iJorvi, @ig_sadhowop

# Active deals in memory
active_deals = {}

# Completed deals history
deal_history = []

# History file path
HISTORY_FILE = "deal_history.json"

# ==========================
# HELPER FUNCTIONS
# ==========================

def escape_markdown(text):
    """
    Escape Markdown special characters to prevent formatting issues.
    Escapes: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    if not text:
        return text
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def load_history():
    """Load deal history from file."""
    global deal_history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r') as f:
                deal_history = json.load(f)
            print(f"✅ Loaded {len(deal_history)} deals from history")
    except Exception as e:
        print(f"⚠️ Error loading history: {e}")
        deal_history = []

def save_history():
    """Save deal history to file."""
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(deal_history, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving history: {e}")

def generate_trade_id():
    """Generate a random Trade ID."""
    chars = string.ascii_uppercase + string.digits
    return "#TID" + "".join(random.choices(chars, k=6))

def initialize_db_pool():
    """Initialize database connection pool for better performance."""
    global db_pool
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    
    try:
        db_pool = pool.SimpleConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL
        )
        print("✅ Database connection pool initialized (2-10 connections)")
        ensure_tables_exist()
    except Exception as e:
        print(f"❌ Failed to initialize database pool: {e}")
        db_pool = None

def ensure_tables_exist():
    """Create required tables if they don't exist."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS active_deals (
                trade_id TEXT PRIMARY KEY,
                buyer TEXT,
                buyer_id TEXT,
                buyer_display TEXT,
                seller TEXT,
                seller_id TEXT,
                seller_display TEXT,
                deal_amount NUMERIC,
                received_amount NUMERIC,
                fee_percent NUMERIC,
                fee_amount NUMERIC,
                release_amount NUMERIC,
                escrow_admin TEXT,
                escrow_admin_name TEXT,
                escrow_admin_id BIGINT,
                source_message_id BIGINT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("ALTER TABLE active_deals ADD COLUMN IF NOT EXISTS buyer_display TEXT")
        cur.execute("ALTER TABLE active_deals ADD COLUMN IF NOT EXISTS seller_display TEXT")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS deal_history (
                id SERIAL PRIMARY KEY,
                trade_id TEXT,
                buyer TEXT,
                buyer_id TEXT,
                buyer_display TEXT,
                seller TEXT,
                seller_id TEXT,
                seller_display TEXT,
                deal_amount NUMERIC,
                received_amount NUMERIC,
                fee_amount NUMERIC,
                release_amount NUMERIC,
                escrow_admin TEXT,
                escrow_admin_id TEXT,
                escrow_admin_name TEXT,
                status TEXT,
                created_at TIMESTAMP,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("ALTER TABLE deal_history ADD COLUMN IF NOT EXISTS buyer_display TEXT")
        cur.execute("ALTER TABLE deal_history ADD COLUMN IF NOT EXISTS seller_display TEXT")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS manual_stats (
                username TEXT PRIMARY KEY,
                user_id TEXT,
                total_volume NUMERIC DEFAULT 0,
                completed_deals INTEGER DEFAULT 0,
                highest_deal NUMERIC DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        cur.close()
        print("✅ Database tables verified/created")
    except Exception as e:
        print(f"⚠️ Error ensuring tables exist: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
    finally:
        if conn:
            return_db_connection(conn)

MANUAL_STATS_FILE = "manual_stats.json"

def load_manual_stats_json():
    """Load manual stats from JSON file."""
    try:
        if os.path.exists(MANUAL_STATS_FILE):
            with open(MANUAL_STATS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Error loading manual stats JSON: {e}")
    return {}

def save_manual_stats_json(username, user_id, total_volume, completed_deals, highest_deal):
    """Save manual stats to JSON file as fallback."""
    try:
        all_stats = load_manual_stats_json()
        all_stats[username.lower()] = {
            "username": username.lower(),
            "user_id": str(user_id) if user_id else None,
            "total_volume": total_volume,
            "completed_deals": completed_deals,
            "highest_deal": highest_deal
        }
        with open(MANUAL_STATS_FILE, 'w') as f:
            json.dump(all_stats, f, indent=2)
        print(f"💾 Saved manual stats for {username} to JSON")
        return True
    except Exception as e:
        print(f"⚠️ Error saving manual stats to JSON: {e}")
        return False

def save_manual_stats(username, user_id, total_volume, completed_deals, highest_deal):
    """Save or update manual stats for a user. Falls back to JSON if DB fails."""
    conn = None
    db_error = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Ensure table exists before inserting
        cur.execute("""
            CREATE TABLE IF NOT EXISTS manual_stats (
                username TEXT PRIMARY KEY,
                user_id TEXT,
                total_volume NUMERIC DEFAULT 0,
                completed_deals INTEGER DEFAULT 0,
                highest_deal NUMERIC DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            INSERT INTO manual_stats (username, user_id, total_volume, completed_deals, highest_deal, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (username) DO UPDATE SET
                user_id = COALESCE(EXCLUDED.user_id, manual_stats.user_id),
                total_volume = EXCLUDED.total_volume,
                completed_deals = EXCLUDED.completed_deals,
                highest_deal = EXCLUDED.highest_deal,
                updated_at = CURRENT_TIMESTAMP
        """, (username.lower(), str(user_id) if user_id else None, total_volume, completed_deals, highest_deal))
        conn.commit()
        cur.close()
        print(f"💾 Saved manual stats for {username}")
        return True, None
    except Exception as e:
        db_error = str(e)
        print(f"⚠️ Error saving manual stats to DB: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        # Fallback to JSON
        json_ok = save_manual_stats_json(username, user_id, total_volume, completed_deals, highest_deal)
        if json_ok:
            return True, "saved_to_json"
        return False, db_error
    finally:
        if conn:
            return_db_connection(conn)

def fetch_manual_stats(username_lower):
    """Fetch manual stats for a user. Falls back to JSON if DB fails."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM manual_stats WHERE username = %s", (username_lower,))
        result = cur.fetchone()
        cur.close()
        if result:
            return result
    except Exception as e:
        print(f"⚠️ Error fetching manual stats from DB: {e}")
    finally:
        if conn:
            return_db_connection(conn)
    
    # Fallback to JSON
    all_stats = load_manual_stats_json()
    return all_stats.get(username_lower)

def parse_stats_message(text):
    """Parse a full stats-formatted message into a dict of values."""
    result = {}

    total_volume_match = re.search(r"Total\s*Volume\s*[:=]?\s*\$?([\d,]+\.?\d*)", text, re.IGNORECASE)
    if total_volume_match:
        result['total_volume'] = float(total_volume_match.group(1).replace(',', ''))

    completed_deals_match = re.search(r"Completed\s*Deals\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if completed_deals_match:
        result['completed_deals'] = int(completed_deals_match.group(1))

    highest_deal_match = re.search(r"Highest\s*Deal\s*[:=]?\s*\$?([\d,]+\.?\d*)", text, re.IGNORECASE)
    if highest_deal_match:
        result['highest_deal'] = float(highest_deal_match.group(1).replace(',', ''))

    ranking_match = re.search(r"Ranking\s*[:=]?\s*#?(\d+|N/A)", text, re.IGNORECASE)
    if ranking_match:
        result['ranking'] = ranking_match.group(1)

    ongoing_deals_match = re.search(r"Ongoing\s*Deals\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if ongoing_deals_match:
        result['ongoing_deals'] = int(ongoing_deals_match.group(1))

    return result

async def _try_process_full_stats_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If the message is a full stats format, save all parsed values and end the conversation."""
    text = update.message.text or ''
    parsed = parse_stats_message(text)

    # Only treat as a full stats message if it contains the three main saveable values
    if not (parsed.get('total_volume') is not None and parsed.get('completed_deals') is not None and parsed.get('highest_deal') is not None):
        return False

    username = context.user_data.get('addstat_username')
    user_id = context.user_data.get('addstat_user_id')
    volume = parsed['total_volume']
    deals = parsed['completed_deals']
    highest = parsed['highest_deal']

    success, error_info = save_manual_stats(username, user_id, volume, deals, highest)

    if success:
        note = " (saved to local file - DB unavailable)" if error_info == "saved_to_json" else ""
        ranking_line = f"👑 Ranking: #{parsed['ranking']}\n" if parsed.get('ranking') and parsed['ranking'].upper() != 'N/A' else ""
        ongoing_line = f"⏳ Ongoing Deals: {parsed['ongoing_deals']}\n" if parsed.get('ongoing_deals') is not None else ""
        await update.message.reply_text(
            f"✅ Stats updated for {username}{note}\n\n"
            f"{ranking_line}"
            f"📈 Total Volume: ${volume:,.2f}\n"
            f"🔢 Completed Deals: {deals}\n"
            f"{ongoing_line}"
            f"⚡ Highest Deal: ${highest:,.2f}"
        )
    else:
        await update.message.reply_text(f"❌ Failed to save stats.\nError: {error_info}")

    # Clean up user_data
    for key in ['addstat_username', 'addstat_user_id', 'addstat_volume', 'addstat_deals']:
        context.user_data.pop(key, None)

    return True

def get_db_connection():
    """Get a database connection from the pool with health check."""
    global db_pool
    
    if db_pool is None:
        initialize_db_pool()
    
    if db_pool:
        try:
            conn = db_pool.getconn()
            # Health check: test if connection is still alive
            if conn.closed:
                print("⚠️ Pool returned a closed connection, getting fresh one...")
                db_pool.putconn(conn, close=True)
                conn = db_pool.getconn()
            else:
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                except Exception:
                    print("⚠️ Pool connection stale, reconnecting...")
                    try:
                        db_pool.putconn(conn, close=True)
                    except:
                        pass
                    conn = psycopg2.connect(DATABASE_URL)
            return conn
        except Exception as e:
            print(f"⚠️ Error getting connection from pool: {e}")
            try:
                return psycopg2.connect(DATABASE_URL)
            except Exception as e2:
                print(f"❌ Direct connection also failed: {e2}")
                raise
    else:
        return psycopg2.connect(DATABASE_URL)

def return_db_connection(conn):
    """Return a connection back to the pool, or close if it's a fallback connection."""
    global db_pool
    if not conn:
        return
    
    if db_pool:
        try:
            db_pool.putconn(conn)
        except Exception as e:
            # Connection not from pool (or pool error), close it directly
            print(f"⚠️ Error returning connection to pool, closing directly: {e}")
            try:
                conn.close()
            except:
                pass
    else:
        # No pool available, this is a fallback connection - must close it
        try:
            conn.close()
        except Exception as e:
            print(f"⚠️ Error closing fallback connection: {e}")

def load_active_deals_from_db():
    """Load all active deals from database into memory."""
    global active_deals
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM active_deals")
        rows = cur.fetchall()
        
        active_deals = {}
        for row in rows:
            trade_id = row['trade_id']
            active_deals[trade_id] = {
                'buyer': row['buyer'],
                'buyer_id': row['buyer_id'],
                'buyer_display': row.get('buyer_display'),
                'seller': row['seller'],
                'seller_id': row['seller_id'],
                'seller_display': row.get('seller_display'),
                'deal_amount': float(row['deal_amount']) if row['deal_amount'] else 0,
                'received_amount': float(row['received_amount']) if row['received_amount'] else 0,
                'fee_percent': float(row['fee_percent']) if row['fee_percent'] else None,
                'fee_amount': float(row['fee_amount']) if row['fee_amount'] else None,
                'release_amount': float(row['release_amount']) if row['release_amount'] else None,
                'escrow_admin': row['escrow_admin'],
                'escrow_admin_name': row['escrow_admin_name'],
                'escrow_admin_id': row['escrow_admin_id'],
                'source_message_id': row['source_message_id'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None
            }
        
        cur.close()
        print(f"✅ Loaded {len(active_deals)} active deals from database")
    except Exception as e:
        print(f"⚠️ Error loading active deals from database: {e}")
        active_deals = {}
    finally:
        if conn:
            return_db_connection(conn)

def save_active_deal_to_db(trade_id, deal_data):
    """Save a single active deal to database."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO active_deals (
                trade_id, buyer, buyer_id, buyer_display, seller, seller_id, seller_display,
                deal_amount, received_amount, fee_percent, fee_amount, release_amount,
                escrow_admin, escrow_admin_name, escrow_admin_id,
                source_message_id, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_id) DO UPDATE SET
                buyer = EXCLUDED.buyer,
                buyer_id = EXCLUDED.buyer_id,
                buyer_display = EXCLUDED.buyer_display,
                seller = EXCLUDED.seller,
                seller_id = EXCLUDED.seller_id,
                seller_display = EXCLUDED.seller_display,
                deal_amount = EXCLUDED.deal_amount,
                received_amount = EXCLUDED.received_amount,
                fee_percent = EXCLUDED.fee_percent,
                fee_amount = EXCLUDED.fee_amount,
                release_amount = EXCLUDED.release_amount,
                source_message_id = EXCLUDED.source_message_id,
                updated_at = CURRENT_TIMESTAMP
        """, (
            trade_id,
            deal_data['buyer'],
            deal_data.get('buyer_id'),
            deal_data.get('buyer_display'),
            deal_data['seller'],
            deal_data.get('seller_id'),
            deal_data.get('seller_display'),
            deal_data['deal_amount'],
            deal_data['received_amount'],
            deal_data.get('fee_percent'),
            deal_data.get('fee_amount'),
            deal_data.get('release_amount'),
            deal_data['escrow_admin'],
            deal_data['escrow_admin_name'],
            deal_data['escrow_admin_id'],
            deal_data.get('source_message_id'),
            deal_data['created_at']
        ))
        
        conn.commit()
        cur.close()
        print(f"💾 Saved deal {trade_id} to database")
    except Exception as e:
        print(f"⚠️ Error saving deal to database: {e}")
    finally:
        if conn:
            return_db_connection(conn)

def delete_active_deal_from_db(trade_id):
    """Delete an active deal from database."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM active_deals WHERE trade_id = %s", (trade_id,))
        conn.commit()
        cur.close()
        print(f"🗑️ Deleted deal {trade_id} from database")
    except Exception as e:
        print(f"⚠️ Error deleting deal from database: {e}")
    finally:
        if conn:
            return_db_connection(conn)

def save_deal_to_history_db(deal_data):
    """Save a completed/refunded deal to history table."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO deal_history (
                trade_id, buyer, buyer_id, buyer_display, seller, seller_id, seller_display,
                deal_amount, received_amount, fee_amount, release_amount,
                escrow_admin, escrow_admin_id, escrow_admin_name,
                status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            deal_data['trade_id'],
            deal_data['buyer'],
            deal_data.get('buyer_id'),
            deal_data.get('buyer_display'),
            deal_data['seller'],
            deal_data.get('seller_id'),
            deal_data.get('seller_display'),
            deal_data['deal_amount'],
            deal_data.get('received_amount'),
            deal_data.get('fee_amount'),
            deal_data.get('release_amount'),
            deal_data['escrow_admin'],
            deal_data.get('escrow_admin_id'),
            deal_data.get('escrow_admin_name'),
            deal_data['status'],
            deal_data.get('created_at')
        ))
        
        conn.commit()
        cur.close()
        print(f"📚 Saved deal {deal_data['trade_id']} to history")
    except Exception as e:
        print(f"⚠️ Error saving deal to history: {e}")
    finally:
        if conn:
            return_db_connection(conn)

def move_deal_to_history_db(trade_id, deal_data):
    """
    Move a deal from active to history in a single transaction.
    This prevents data loss if either operation fails.
    Returns True if successful, False otherwise.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Start transaction (autocommit is off by default)
        # 1. Insert into history
        cur.execute("""
            INSERT INTO deal_history (
                trade_id, buyer, buyer_id, buyer_display, seller, seller_id, seller_display,
                deal_amount, received_amount, fee_amount, release_amount,
                escrow_admin, escrow_admin_id, escrow_admin_name,
                status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            deal_data['trade_id'],
            deal_data['buyer'],
            deal_data.get('buyer_id'),
            deal_data.get('buyer_display'),
            deal_data['seller'],
            deal_data.get('seller_id'),
            deal_data.get('seller_display'),
            deal_data['deal_amount'],
            deal_data.get('received_amount'),
            deal_data.get('fee_amount'),
            deal_data.get('release_amount'),
            deal_data['escrow_admin'],
            deal_data.get('escrow_admin_id'),
            deal_data.get('escrow_admin_name'),
            deal_data['status'],
            deal_data.get('created_at')
        ))
        
        # 2. Delete from active deals
        cur.execute("DELETE FROM active_deals WHERE trade_id = %s", (trade_id,))
        
        # Commit transaction (both operations succeed or both fail)
        conn.commit()
        cur.close()
        
        print(f"✅ Successfully moved deal {trade_id} to history (status: {deal_data['status']})")
        return True
        
    except Exception as e:
        print(f"❌ Error moving deal to history: {e}")
        try:
            if conn:
                conn.rollback()
        except:
            pass
        return False
    finally:
        if conn:
            return_db_connection(conn)

def fetch_user_lifetime_stats(user_id, username_lower):
    """
    Fetch lifetime statistics for a user from the database.
    Searches by user_id (primary) and username (fallback for old records).
    If user_id is None, searches by username only (for looking up other users).
    Returns a dict with stats or None if database unavailable.
    Falls back to JSON file if database fails.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Convert user_id to string for database comparison (if provided)
        user_id_str = str(user_id) if user_id else None
        
        # Fetch all COMPLETED deals where user is buyer or seller
        # Search by user_id first (never changes), then fallback to username (for old records)
        if user_id_str:
            # Have user_id, search by both ID and username
            cur.execute("""
                SELECT 
                    COUNT(*) as total_deals,
                    COALESCE(SUM(deal_amount), 0) as total_volume,
                    COALESCE(MAX(deal_amount), 0) as highest_deal
                FROM deal_history
                WHERE (
                    buyer_id = %s OR seller_id = %s OR
                    LOWER(buyer) = %s OR LOWER(seller) = %s
                )
                AND status = 'completed'
            """, (user_id_str, user_id_str, username_lower, username_lower))
        else:
            # No user_id, search by username only
            cur.execute("""
                SELECT 
                    COUNT(*) as total_deals,
                    COALESCE(SUM(deal_amount), 0) as total_volume,
                    COALESCE(MAX(deal_amount), 0) as highest_deal
                FROM deal_history
                WHERE (LOWER(buyer) = %s OR LOWER(seller) = %s)
                AND status = 'completed'
            """, (username_lower, username_lower))
        
        result = cur.fetchone()
        
        # Fetch ranking data - total volume per user (only completed deals)
        # Aggregate by username to combine deals with/without user_id
        cur.execute("""
            SELECT 
                LOWER(buyer) as username,
                SUM(deal_amount) as volume
            FROM deal_history
            WHERE status = 'completed'
            GROUP BY LOWER(buyer)
            UNION ALL
            SELECT 
                LOWER(seller) as username,
                SUM(deal_amount) as volume
            FROM deal_history
            WHERE status = 'completed'
            GROUP BY LOWER(seller)
        """)
        
        ranking_rows = cur.fetchall()
        
        cur.close()
        
        # Aggregate volumes per user for ranking
        user_volumes = {}
        for row in ranking_rows:
            username = row['username']
            volume = float(row['volume']) if row['volume'] else 0
            user_volumes[username] = user_volumes.get(username, 0) + volume
        
        # Sort and find rank
        sorted_users = sorted(user_volumes.items(), key=lambda x: x[1], reverse=True)
        ranking = None
        for idx, (uname, vol) in enumerate(sorted_users, 1):
            # Match by username
            if uname == username_lower:
                ranking = idx
                break
        
        # If user has no deals, show "N/A" for ranking
        if ranking is None or (result and int(result['total_deals']) == 0):
            ranking = "N/A"
        
        stats = {
            'total_deals': int(result['total_deals']) if result else 0,
            'total_volume': float(result['total_volume']) if result else 0.0,
            'highest_deal': float(result['highest_deal']) if result else 0.0,
            'ranking': ranking,
            'source': 'database'
        }
        
        print(f"📊 Fetched stats from database: {stats}")
        return stats
        
    except Exception as e:
        print(f"⚠️ Error fetching stats from database: {e}")
        print("📁 Falling back to JSON file...")
        
        # Fallback to JSON file (only count completed deals)
        global deal_history
        total_volume = 0.0
        total_deals = 0
        highest_deal = 0.0
        
        for deal in deal_history:
            # Only count completed deals, not refunded
            if deal.get('status') != 'completed':
                continue
                
            buyer = deal.get('buyer', '').lower()
            seller = deal.get('seller', '').lower()
            if buyer == username_lower or seller == username_lower:
                total_deals += 1
                total_volume += deal['deal_amount']
                if deal['deal_amount'] > highest_deal:
                    highest_deal = deal['deal_amount']
        
        # Calculate ranking from JSON (only completed deals)
        all_users_volume = {}
        for deal in deal_history:
            # Only count completed deals for ranking
            if deal.get('status') != 'completed':
                continue
                
            for participant in [deal.get('buyer'), deal.get('seller')]:
                if participant:
                    participant_lower = participant.lower()
                    all_users_volume[participant_lower] = all_users_volume.get(participant_lower, 0) + deal['deal_amount']
        
        sorted_users = sorted(all_users_volume.items(), key=lambda x: x[1], reverse=True)
        ranking = None
        for idx, (uname, vol) in enumerate(sorted_users, 1):
            if uname == username_lower:
                ranking = idx
                break
        
        # If user has no deals, show "N/A" for ranking
        if ranking is None or total_deals == 0:
            ranking = "N/A"
        
        return {
            'total_deals': total_deals,
            'total_volume': total_volume,
            'highest_deal': highest_deal,
            'ranking': ranking,
            'source': 'json_fallback'
        }
    finally:
        if conn:
            return_db_connection(conn)

async def resolve_user_id_from_username(username):
    """Resolve user ID from username using Telethon."""
    global telethon_client
    
    try:
        if not telethon_client:
            return None
        
        # Remove @ if present
        username = username.lstrip('@')
        
        # Get user by username
        user = await telethon_client.get_entity(username)
        return str(user.id)
    except Exception as e:
        print(f"⚠️ Could not resolve username {username}: {e}")
        return None

async def check_user_bio(bot, user_id):
    """Check if user has @Escrow_PagaL in their bio using Telethon with caching."""
    global telethon_client, bio_cache, bio_cache_lock
    
    try:
        # Short-circuit checks
        if not user_id:
            return False
        
        if telethon_client is None:
            return False
        
        user_id = int(user_id)
        current_time = datetime.now().timestamp()
        
        # Check cache first
        async with bio_cache_lock:
            if user_id in bio_cache:
                cached = bio_cache[user_id]
                age = current_time - cached["fetched_at"]
                if age < BIO_CACHE_TTL:
                    print(f"✅ Cache hit for user {user_id} (age: {int(age)}s)")
                    return cached["has_pagal"]
                else:
                    print(f"⏰ Cache expired for user {user_id} (age: {int(age)}s)")
        
        # Cache miss or expired - fetch from Telegram
        print(f"🔍 Fetching bio for user {user_id} via Telethon...")
        
        try:
            user_entity = await telethon_client.get_entity(user_id)
            full_user = await telethon_client(GetFullUserRequest(user_entity))
            bio = getattr(full_user.full_user, 'about', '') or ''
            
            has_pagal = "@Escrow_PagaL" in bio or "@Escrow_Pagal" in bio
            
            # Update cache
            async with bio_cache_lock:
                bio_cache[user_id] = {
                    "has_pagal": has_pagal,
                    "fetched_at": current_time
                }
            
            if has_pagal:
                print(f"✅ User {user_id} has @Escrow_PagaL in bio")
            else:
                print(f"❌ User {user_id} does NOT have @Escrow_PagaL in bio")
            
            return has_pagal
        
        except FloodWaitError as e:
            print(f"⚠️ Rate limited, waiting {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            return False
        except Exception as e:
            print(f"⚠️ Could not check bio for user {user_id}: {type(e).__name__}: {e}")
            return False
    
    except Exception as e:
        print(f"⚠️ Error in check_user_bio for user {user_id}: {e}")
        return False

async def try_get_user_id(context, chat_id, username):
    """Try to get user ID from username by checking chat members."""
    try:
        # Remove @ if present
        username = username.lstrip('@')
        
        # Try to get chat member by username
        # Note: This only works in groups/supergroups
        chat = await context.bot.get_chat(chat_id)
        
        # For private chats or channels, we can't enumerate members
        # So we'll return None
        return None
    except Exception as e:
        print(f"Could not resolve username {username}: {e}")
        return None

def _user_mention_html(user):
    """Build a clickable HTML mention for a Telegram user object."""
    if not user:
        return None
    if user.username:
        display = f"@{user.username}"
    else:
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        if not full_name:
            full_name = "User"
        else:
            full_name = html.escape(full_name)
        display = full_name
    return f'<a href="tg://user?id={user.id}">{display}</a>'

def parse_deal_text(text, entities=None, sender_user=None):
    """Extract deal info from the format message."""
    # Try to extract username or a case-sensitive "Me" self-reference and optional user ID from text
    buyer_match = re.search(r"BUYER\s*:\s*(@\w+|Me)(?:\s*[\[\(]?(\d+)[\]\)]?)?", text, re.IGNORECASE)
    seller_match = re.search(r"SELLER\s*:\s*(@\w+|Me)(?:\s*[\[\(]?(\d+)[\]\)]?)?", text, re.IGNORECASE)
    amount = re.search(r"DEAL AMOUNT\s*:\s*\$?([\d.]+)", text, re.IGNORECASE)

    buyer_username = buyer_match.group(1) if buyer_match else None
    seller_username = seller_match.group(1) if seller_match else None

    # First try to get IDs from text (manually written)
    buyer_id = buyer_match.group(2) if buyer_match and buyer_match.group(2) else None
    seller_id = seller_match.group(2) if seller_match and seller_match.group(2) else None

    buyer_display = None
    seller_display = None

    # Resolve "Me" / "me" / "ME" self-references (case-insensitive except real @me username)
    def resolve_me(value):
        if value and sender_user:
            clean = value.lstrip('@')
            if clean.lower() == "me" and not (value.startswith('@') and clean == 'me'):
                return (
                    f"@{sender_user.username}" if sender_user.username else f"ID:{sender_user.id}",
                    str(sender_user.id),
                    _user_mention_html(sender_user)
                )
        return value, None, None

    buyer_username, me_buyer_id, buyer_display = resolve_me(buyer_username)
    if me_buyer_id:
        buyer_id = me_buyer_id

    seller_username, me_seller_id, seller_display = resolve_me(seller_username)
    if me_seller_id:
        seller_id = me_seller_id

    # If not found in text, try to extract from message entities (text_mention type)
    if entities and (not buyer_id or not seller_id):
        for entity in entities:
            if entity.type == "text_mention" and entity.user:
                # This is when user is mentioned using the dropdown (provides user object)
                user_id = str(entity.user.id)
                offset = entity.offset

                # Check if this mention is near "BUYER" or "SELLER"
                context = text[max(0, offset-20):offset+20].upper()
                if "BUYER" in context and not buyer_id:
                    buyer_id = user_id
                elif "SELLER" in context and not seller_id:
                    seller_id = user_id

    return {
        "buyer": buyer_username,
        "buyer_id": buyer_id,
        "buyer_display": buyer_display,
        "seller": seller_username,
        "seller_id": seller_id,
        "seller_display": seller_display,
        "amount": float(amount.group(1)) if amount else None
    }

# ==========================
# /ADD COMMAND
# ==========================

async def add_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when admin replies with /add (received_amount)."""
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Please reply to a deal message that follows your format to use /add.\n\nUsage: /add <received_amount>\nExample: /add 550")
        return

    user = update.message.from_user
    print(f"DEBUG: Triggered by {user.username} (ID: {user.id})")

    if user.id not in ADMINS:
        await update.message.reply_text(f"🚫 Only authorized admins can add deals.\n\nYour ID: {user.id}")
        return

    deal_text = update.message.reply_to_message.text
    entities = update.message.reply_to_message.entities
    sender_user = update.message.reply_to_message.from_user
    info = parse_deal_text(deal_text, entities, sender_user)

    if not info["buyer"] or not info["seller"] or not info["amount"]:
        await update.message.reply_text("❌ Could not parse deal details. Make sure your message matches this format:\n\n"
                                        "DEAL INFO : USD INR EXCHANGE\nBUYER : @username\nSELLER : @username\nDEAL AMOUNT : $100\nTIME TO COMPLETE DEAL :")
        return

    received_amount = None
    if context.args:
        try:
            received_amount = float(context.args[0])
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid received amount. Please provide a valid number.\n\nUsage: /add <received_amount>\nExample: /add 550")
            return
    else:
        received_amount = info["amount"]

    # Create and store temporary deal data
    trade_id = generate_trade_id()
    admin_first_name = user.first_name or ""
    admin_last_name = user.last_name or ""
    admin_full_name = f"{admin_first_name} {admin_last_name}".strip() or "Admin"
    
    active_deals[trade_id] = {
        "buyer": info["buyer"],
        "buyer_id": info["buyer_id"],
        "buyer_display": info.get("buyer_display"),
        "seller": info["seller"],
        "seller_id": info["seller_id"],
        "seller_display": info.get("seller_display"),
        "deal_amount": info["amount"],
        "received_amount": received_amount,
        "escrow_admin": f"@{user.username}" if user.username else f"ID:{user.id}",
        "escrow_admin_name": admin_full_name,
        "escrow_admin_id": user.id,
        "created_at": datetime.now().isoformat(),
        "source_message_id": update.message.reply_to_message.message_id
    }

    # Try to resolve user IDs from usernames if not provided
    buyer_id = info["buyer_id"]
    seller_id = info["seller_id"]
    
    print(f"DEBUG: Buyer username: {info['buyer']}, ID from parse: {buyer_id}")
    print(f"DEBUG: Seller username: {info['seller']}, ID from parse: {seller_id}")
    
    if not buyer_id and info["buyer"] and telethon_client:
        buyer_id = await resolve_user_id_from_username(info["buyer"])
        print(f"DEBUG: Resolved buyer ID from username: {buyer_id}")
    
    if not seller_id and info["seller"] and telethon_client:
        seller_id = await resolve_user_id_from_username(info["seller"])
        print(f"DEBUG: Resolved seller ID from username: {seller_id}")
    
    # Update the active deal with resolved user IDs
    active_deals[trade_id]["buyer_id"] = buyer_id
    active_deals[trade_id]["seller_id"] = seller_id
    
    # Save deal to database immediately (before fee selection)
    # This ensures persistence even if bot restarts before fee is chosen
    save_active_deal_to_db(trade_id, active_deals[trade_id])
    
    # Check if both users have @Escrow_PagaL in their bio (run in parallel for speed)
    buyer_task = check_user_bio(context.bot, buyer_id)
    seller_task = check_user_bio(context.bot, seller_id)
    results = await asyncio.gather(buyer_task, seller_task, return_exceptions=True)
    
    # Extract results with fallback for exceptions
    buyer_has_pagal = results[0] if not isinstance(results[0], Exception) else False
    seller_has_pagal = results[1] if not isinstance(results[1], Exception) else False
    
    print(f"DEBUG: Buyer has @Escrow_PagaL: {buyer_has_pagal}")
    print(f"DEBUG: Seller has @Escrow_PagaL: {seller_has_pagal}")
    
    if buyer_has_pagal and seller_has_pagal:
        # Both have @Escrow_PagaL, set 0% fee automatically
        print(f"DEBUG: Both users have @Escrow_PagaL in bio - applying 0% fee")
        
        deal_amount = info["amount"]
        fee = 0.0
        release_amount = received_amount
        
        active_deals[trade_id]["fee_percent"] = 0.0
        active_deals[trade_id]["fee_amount"] = fee
        active_deals[trade_id]["release_amount"] = release_amount
        
        # Save deal to database
        save_active_deal_to_db(trade_id, active_deals[trade_id])
        
        buyer_info = info.get("buyer_display") or f"{info['buyer']}"
        if buyer_id:
            buyer_info += f" [{buyer_id}]"

        seller_info = info.get("seller_display") or f"{info['seller']}"
        if seller_id:
            seller_info += f" [{seller_id}]"

        msg = (
            f"<tg-emoji emoji-id='5987880246865565644'>💰</tg-emoji> <b>Deal Amount:</b> ${deal_amount:.2f}\n"
            f"<tg-emoji emoji-id='5877307202888273539'>📤</tg-emoji> <b>Received Amount:</b> ${received_amount:.2f}\n"
            f"<tg-emoji emoji-id='5967548335542767952'>📤</tg-emoji> <b>Release/Refund Amount:</b> ${release_amount:.2f}\n"
            f"<tg-emoji emoji-id='5936017305585586269'>🆔</tg-emoji> <b>Trade ID:</b> {trade_id}\n\n"
            f"<b>Continue the Deal</b>\n"
            f"<b>Buyer:</b> {buyer_info}\n"
            f"<b>Seller:</b> {seller_info}\n\n"
            f"<tg-emoji emoji-id='5920052658743283381'>🛡</tg-emoji> <b>Escrowed By:</b> {active_deals[trade_id]['escrow_admin']}"
        )
        
        await update.message.reply_to_message.reply_text(msg, parse_mode="HTML")
    else:
        # One or both don't have @Escrow_PagaL, ask for fee selection
        keyboard = [
            [
                InlineKeyboardButton("Fee: 0.7%", callback_data=f"fee_0.7_{trade_id}"),
                InlineKeyboardButton("Fee: 1%", callback_data=f"fee_1_{trade_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_to_message.reply_text(
            "Please select a fee for this deal:",
            reply_markup=reply_markup
        )
    
    # Delete the /add command message
    try:
        await update.message.delete()
    except Exception as e:
        print(f"⚠️ Could not delete /add command message: {e}")

# ==========================
# FEE SELECTION HANDLER
# ==========================

async def fee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when one of the fee buttons is pressed."""
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    fee_percent = float(data[1])
    trade_id = "_".join(data[2:])  # Join remaining parts in case trade_id has underscores

    deal = active_deals.get(trade_id)
    if not deal:
        await query.edit_message_text("⚠️ Deal not found. Please try again.")
        return

    deal_amount = deal["deal_amount"]
    received_amount = deal.get("received_amount", deal_amount)
    fee = round(received_amount * (fee_percent / 100), 2)
    release_amount = round(received_amount - fee, 2)

    # Store final deal data
    deal["fee_percent"] = fee_percent
    deal["fee_amount"] = fee
    deal["release_amount"] = release_amount
    
    # Save deal to database
    save_active_deal_to_db(trade_id, deal)

    buyer_info = deal.get('buyer_display') or f"{deal['buyer']}"
    buyer_id = deal.get('buyer_id')
    if buyer_id:
        buyer_info += f" [{buyer_id}]"

    seller_info = deal.get('seller_display') or f"{deal['seller']}"
    seller_id = deal.get('seller_id')
    if seller_id:
        seller_info += f" [{seller_id}]"

    msg = (
        f"<tg-emoji emoji-id='5987880246865565644'>💰</tg-emoji> <b>Deal Amount:</b> ${deal_amount:.2f}\n"
        f"<tg-emoji emoji-id='5877307202888273539'>📤</tg-emoji> <b>Received Amount:</b> ${received_amount:.2f}\n"
        f"<tg-emoji emoji-id='5967548335542767952'>📤</tg-emoji> <b>Release/Refund Amount:</b> ${release_amount:.2f}\n"
        f"<tg-emoji emoji-id='5936017305585586269'>🆔</tg-emoji> <b>Trade ID:</b> {trade_id}\n\n"
        f"<b>Continue the Deal</b>\n"
        f"<b>Buyer:</b> {buyer_info}\n"
        f"<b>Seller:</b> {seller_info}\n\n"
        f"<tg-emoji emoji-id='5920052658743283381'>🛡</tg-emoji> <b>Escrowed By:</b> {deal['escrow_admin']}"
    )

    await query.edit_message_text(msg, parse_mode="HTML")

# ==========================
# /CLOSE COMMAND
# ==========================

async def close_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when admin closes a deal."""
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Please reply to a deal message to use /close.")
        return

    user = update.message.from_user
    print(f"DEBUG: Close triggered by {user.username} (ID: {user.id})")

    if user.id not in ADMINS:
        await update.message.reply_text("🚫 Only authorized admins can close deals.")
        return

    replied_text = update.message.reply_to_message.text
    replied_message_id = update.message.reply_to_message.message_id

    # 1️⃣ Try to find Trade ID in replied message
    trade_id_match = re.search(r"#TID[A-Z0-9]+", replied_text)

    # 2️⃣ If not found, try to match by message ID
    if not trade_id_match:
        found_id = None
        for tid, deal in active_deals.items():
            if deal.get("source_message_id") == replied_message_id:
                found_id = tid
                print(f"DEBUG: Found deal {tid} by matching message_id {replied_message_id}")
                break
        
        # 3️⃣ Last resort: fallback to text matching
        if not found_id:
            for tid, deal in active_deals.items():
                if (deal["buyer"] and deal["buyer"] in replied_text) or (deal["seller"] and deal["seller"] in replied_text):
                    found_id = tid
                    print(f"⚠️ WARNING: Found deal {tid} by text matching (not message_id). This may be incorrect!")
                    break
        
        if found_id:
            trade_id = found_id
        else:
            await update.message.reply_text("❌ Could not find Trade ID in that deal.")
            return
    else:
        trade_id = trade_id_match.group(0)

    deal = active_deals.get(trade_id)
    if not deal:
        await update.message.reply_text("⚠️ No record found for this trade.")
        return

    buyer_info = deal.get('buyer_display') or f"{deal['buyer']}"
    seller_info = deal.get('seller_display') or f"{deal['seller']}"

    buyer_vouch = deal.get('buyer_display') or f"{deal['buyer']}"
    seller_vouch = deal.get('seller_display') or f"{deal['seller']}"

    msg = (
        f"<tg-emoji emoji-id='5197474765387864959'>✅</tg-emoji> Deal Completed\n"
        f"<tg-emoji emoji-id='5936017305585586269'>🆔</tg-emoji> Trade ID: {trade_id}\n"
        f"<tg-emoji emoji-id='5879785854284599288'>📤</tg-emoji> Released: ${deal['release_amount']:.2f}\n"
        f"<tg-emoji emoji-id='5879785854284599288'>ℹ️</tg-emoji> Total Released: ${deal['release_amount']:.2f}\n\n"
        f"Buyer: {buyer_info}\n"
        f"Seller: {seller_info}\n\n"
        f"<tg-emoji emoji-id='5920052658743283381'>🛡</tg-emoji> Escrowed By: {deal['escrow_admin']}\n\n"
        f"~ {seller_vouch} and {buyer_vouch} are requested to drop the vouch before leaving👇🏻\n\n"
        f"<code>Vouch @PAGALWORLD for ${deal['deal_amount']:.2f} smooth escrow deal❤️</code>"
    )

    await update.message.reply_to_message.reply_text(msg, parse_mode="HTML")

    # Prepare history entry
    history_entry = {
        "trade_id": trade_id,
        "buyer": deal['buyer'],
        "buyer_id": deal.get('buyer_id'),
        "buyer_display": deal.get('buyer_display'),
        "seller": deal['seller'],
        "seller_id": deal.get('seller_id'),
        "seller_display": deal.get('seller_display'),
        "deal_amount": deal['deal_amount'],
        "received_amount": deal.get('received_amount', deal['deal_amount']),
        "fee_amount": deal.get('fee_amount', 0),
        "release_amount": deal.get('release_amount', deal['deal_amount']),
        "escrow_admin": deal['escrow_admin'],
        "escrow_admin_id": deal.get('escrow_admin_id'),
        "escrow_admin_name": deal.get('escrow_admin_name', 'Admin'),
        "created_at": deal.get('created_at'),
        "status": "completed"
    }
    
    # Move deal to history in a transaction-safe manner
    db_success = move_deal_to_history_db(trade_id, history_entry)
    
    if not db_success:
        print(f"⚠️ Transaction move failed for {trade_id}, attempting individual operations...")
        save_deal_to_history_db(history_entry)
        delete_active_deal_from_db(trade_id)
    
    # Always save to JSON history and remove from memory
    deal_history.append(history_entry)
    save_history()
    del active_deals[trade_id]
    
    # Delete the /close command message
    try:
        await update.message.delete()
    except Exception as e:
        print(f"⚠️ Could not delete /close command message: {e}")

# ==========================
# /REFUND COMMAND
# ==========================

async def refund_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when admin refunds a deal."""
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Please reply to a deal message to use /refund.")
        return

    user = update.message.from_user
    print(f"DEBUG: Refund triggered by {user.username} (ID: {user.id})")

    if user.id not in ADMINS:
        await update.message.reply_text("🚫 Only authorized admins can refund deals.")
        return

    replied_text = update.message.reply_to_message.text
    replied_message_id = update.message.reply_to_message.message_id

    # 1️⃣ Try to find Trade ID in replied message
    trade_id_match = re.search(r"#TID[A-Z0-9]+", replied_text)

    # 2️⃣ If not found, try to match by message ID
    if not trade_id_match:
        found_id = None
        for tid, deal in active_deals.items():
            if deal.get("source_message_id") == replied_message_id:
                found_id = tid
                print(f"DEBUG: Found deal {tid} by matching message_id {replied_message_id}")
                break
        
        # 3️⃣ Last resort: fallback to text matching
        if not found_id:
            for tid, deal in active_deals.items():
                if (deal["buyer"] and deal["buyer"] in replied_text) or (deal["seller"] and deal["seller"] in replied_text):
                    found_id = tid
                    print(f"⚠️ WARNING: Found deal {tid} by text matching (not message_id). This may be incorrect!")
                    break
        
        if found_id:
            trade_id = found_id
        else:
            await update.message.reply_text("❌ Could not find Trade ID in that deal.")
            return
    else:
        trade_id = trade_id_match.group(0)

    deal = active_deals.get(trade_id)
    if not deal:
        await update.message.reply_text("⚠️ No record found for this trade.")
        return

    buyer_info = deal.get('buyer_display') or f"{deal['buyer']}"
    if deal.get('buyer_id'):
        buyer_info += f" [{deal['buyer_id']}]"

    seller_info = deal.get('seller_display') or f"{deal['seller']}"
    if deal.get('seller_id'):
        seller_info += f" [{deal['seller_id']}]"

    msg = (
        f"✅ <b>Deal Refunded</b>\n"
        f"🆔 <b>Trade ID:</b> {trade_id}\n"
        f"📤 <b>Refunded:</b> ${deal['release_amount']:.2f}\n"
        f"ℹ️ <b>Total Refunded:</b> ${deal['release_amount']:.2f}\n\n"
        f"<b>Buyer:</b> {buyer_info}\n"
        f"<b>Seller:</b> {seller_info}\n\n"
        f"🛡 <b>Escrowed By:</b> {deal['escrow_admin']}\n"
    )

    await update.message.reply_to_message.reply_text(msg, parse_mode="HTML")

    # Prepare history entry
    history_entry = {
        "trade_id": trade_id,
        "buyer": deal['buyer'],
        "buyer_id": deal.get('buyer_id'),
        "buyer_display": deal.get('buyer_display'),
        "seller": deal['seller'],
        "seller_id": deal.get('seller_id'),
        "seller_display": deal.get('seller_display'),
        "deal_amount": deal['deal_amount'],
        "received_amount": deal.get('received_amount', deal['deal_amount']),
        "fee_amount": deal.get('fee_amount', 0),
        "release_amount": deal.get('release_amount', deal['deal_amount']),
        "escrow_admin": deal['escrow_admin'],
        "escrow_admin_id": deal.get('escrow_admin_id'),
        "escrow_admin_name": deal.get('escrow_admin_name', 'Admin'),
        "created_at": deal.get('created_at'),
        "status": "refunded"
    }
    
    # Move deal to history in a transaction-safe manner
    db_success = move_deal_to_history_db(trade_id, history_entry)
    
    if not db_success:
        print(f"⚠️ Transaction move failed for {trade_id}, attempting individual operations...")
        save_deal_to_history_db(history_entry)
        delete_active_deal_from_db(trade_id)
    
    # Always save to JSON history and remove from memory
    deal_history.append(history_entry)
    save_history()
    del active_deals[trade_id]
    
    # Delete the /refund command message
    try:
        await update.message.delete()
    except Exception as e:
        print(f"⚠️ Could not delete /refund command message: {e}")

# ==========================
# /ACTIVE_DEALS COMMAND
# ==========================

async def show_active_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all currently active deals."""
    user = update.message.from_user
    print(f"DEBUG: Active deals requested by {user.username} (ID: {user.id})")

    if user.id not in ADMINS:
        await update.message.reply_text("🚫 Only authorized admins can view active deals.")
        return

    if not active_deals:
        await update.message.reply_text("📭 No active deals at the moment.")
        return

    msg = "📋 **Active Deals**\n\n"
    
    for trade_id, deal in active_deals.items():
        # Escape Markdown special characters in usernames
        buyer_escaped = escape_markdown(deal['buyer'])
        buyer_info = f"{buyer_escaped}"
        if deal.get('buyer_id'):
            buyer_info += f" [{deal['buyer_id']}]"
        
        seller_escaped = escape_markdown(deal['seller'])
        seller_info = f"{seller_escaped}"
        if deal.get('seller_id'):
            seller_info += f" [{deal['seller_id']}]"
        
        escrow_admin_escaped = escape_markdown(deal['escrow_admin'])
        
        msg += f"🆔 **{trade_id}**\n"
        msg += f"👤 Buyer: {buyer_info}\n"
        msg += f"👤 Seller: {seller_info}\n"
        msg += f"💰 Deal Amount: ${deal['deal_amount']:.2f}\n"
        
        received_amount = deal.get('received_amount', deal['deal_amount'])
        if received_amount != deal['deal_amount']:
            msg += f"📤 Received Amount: ${received_amount:.2f}\n"
        
        if "fee_percent" in deal:
            msg += f"💵 Fee: {deal['fee_percent']}% (${deal['fee_amount']:.2f})\n"
            msg += f"📤 Release Amount: ${deal['release_amount']:.2f}\n"
        else:
            msg += f"⏳ Status: Awaiting fee selection\n"
        
        msg += f"🛡 Escrowed By: {escrow_admin_escaped}\n"
        msg += "─────────────────\n\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

# ==========================
# /STATS COMMAND
# ==========================

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show lifetime user statistics for escrow deals.
    Usage: /stats - Show your own stats
           /stats @username - Show another user's stats
    """
    user = update.message.from_user
    
    # Check if a username was provided as argument
    target_username = None
    target_user_id = None
    
    if context.args and len(context.args) > 0:
        # User wants to check someone else's stats
        target_username = context.args[0]
        if not target_username.startswith('@'):
            target_username = f"@{target_username}"
        target_username_lower = target_username.lower()
        
        print(f"DEBUG: Stats requested by {user.username} for {target_username}")
        
        # We don't have the target user's ID, so we'll search by username only
        stats = fetch_user_lifetime_stats(None, target_username_lower)
    else:
        # User wants to check their own stats
        username = f"@{user.username}" if user.username else None
        
        if not username:
            await update.message.reply_text("⚠️ You need a username to use this command. Please set a username in Telegram settings.")
            return
        
        print(f"DEBUG: Stats requested by {user.username} (ID: {user.id})")
        print(f"DEBUG: Looking for user_id: {user.id}, username: {username}")
        
        username_lower = username.lower()
        target_username = username
        target_username_lower = username_lower
        target_user_id = user.id
        
        # Fetch lifetime stats from database (searches by user_id + username for backward compatibility)
        stats = fetch_user_lifetime_stats(target_user_id, target_username_lower)
    
    # Count ongoing deals from active_deals (search by user_id + username)
    ongoing_deals = 0
    ongoing_volume = 0.0
    for trade_id, deal in active_deals.items():
        buyer = deal.get('buyer', '').lower()
        seller = deal.get('seller', '').lower()
        
        is_match = False
        if target_user_id:
            # If we have user_id, match by both ID and username
            user_id_str = str(target_user_id)
            buyer_id = str(deal.get('buyer_id', ''))
            seller_id = str(deal.get('seller_id', ''))
            if buyer_id == user_id_str or seller_id == user_id_str or buyer == target_username_lower or seller == target_username_lower:
                is_match = True
        else:
            # If we don't have user_id (looking up someone else), match by username only
            if buyer == target_username_lower or seller == target_username_lower:
                is_match = True
        
        if is_match:
            ongoing_deals += 1
            # Ensure deal_amount is float (could be string from JSON)
            deal_amount = deal.get('deal_amount', 0)
            ongoing_volume += float(deal_amount) if deal_amount else 0.0
    
    # Fetch manual stats and add on top of calculated stats
    manual = fetch_manual_stats(target_username_lower)
    manual_volume = float(manual['total_volume']) if manual and manual.get('total_volume') else 0.0
    manual_deals = int(manual['completed_deals']) if manual and manual.get('completed_deals') else 0
    manual_highest = float(manual['highest_deal']) if manual and manual.get('highest_deal') else 0.0
    
    combined_volume = stats['total_volume'] + manual_volume + ongoing_volume
    combined_deals = stats['total_deals'] + manual_deals
    combined_highest = max(stats['highest_deal'], manual_highest)
    
    # Format message
    ranking_display = f"#{stats['ranking']}" if stats['ranking'] != "N/A" else "N/A"
    
    # Escape Markdown special characters in username
    username_escaped = escape_markdown(target_username)
    
    msg = (
        f"📊 **Participant Stats for {username_escaped}**\n\n"
        f"👑 Ranking: {ranking_display}\n"
        f"📈 Total Volume: ${combined_volume:.2f}\n"
        f"🔢 Completed Deals: {combined_deals}\n"
        f"⏳ Ongoing Deals: {ongoing_deals}\n"
        f"⚡ Highest Deal: ${combined_highest:.2f}\n\n"
        f"📊 Always use @Escrow\\_Pagal for safer transactions!"
    )
    
    await update.message.reply_text(msg, parse_mode="Markdown")

# ==========================
# /ADMINWISE COMMAND
# ==========================

async def adminwise_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily escrow statistics for all admins."""
    user_id = update.message.from_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    today = datetime.now().date()
    
    admin_stats = {}
    
    # Calculate stats from active deals (created today)
    for trade_id, deal in active_deals.items():
        created_at = deal.get('created_at')
        if not created_at:
            continue
        
        try:
            deal_date = datetime.fromisoformat(created_at).date()
            if deal_date == today:
                admin_id = deal.get('escrow_admin_id')
                admin_name = deal.get('escrow_admin_name', 'Admin')
                amount = deal.get('deal_amount', 0)
                
                if admin_id:
                    if admin_id not in admin_stats:
                        admin_stats[admin_id] = {
                            'name': admin_name,
                            'total_held': 0,
                            'deals_count': 0
                        }
                    admin_stats[admin_id]['total_held'] += amount
                    admin_stats[admin_id]['deals_count'] += 1
        except (ValueError, AttributeError):
            continue
    
    # Calculate stats from completed deals ONLY (created today, exclude refunded)
    # Use database for accurate stats
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch completed deals from today
        cur.execute("""
            SELECT escrow_admin_id, escrow_admin_name, deal_amount
            FROM deal_history
            WHERE DATE(created_at) = %s
            AND status = 'completed'
        """, (today,))
        
        completed_deals = cur.fetchall()
        
        for deal in completed_deals:
            admin_id = deal.get('escrow_admin_id')
            admin_name = deal.get('escrow_admin_name', 'Admin')
            amount = deal.get('deal_amount', 0)
            
            if admin_id:
                if admin_id not in admin_stats:
                    admin_stats[admin_id] = {
                        'name': admin_name,
                        'total_held': 0,
                        'deals_count': 0
                    }
                admin_stats[admin_id]['total_held'] += amount
                admin_stats[admin_id]['deals_count'] += 1
    
    except Exception as e:
        print(f"⚠️ Error fetching completed deals from database: {e}")
        # Fallback to JSON file (but filter by status='completed')
        for deal in deal_history:
            created_at = deal.get('created_at')
            status = deal.get('status')
            if not created_at or status != 'completed':  # Only count completed, not refunded
                continue
            
            try:
                deal_date = datetime.fromisoformat(created_at).date()
                if deal_date == today:
                    admin_id = deal.get('escrow_admin_id')
                    admin_name = deal.get('escrow_admin_name', 'Admin')
                    amount = deal.get('deal_amount', 0)
                    
                    if admin_id:
                        if admin_id not in admin_stats:
                            admin_stats[admin_id] = {
                                'name': admin_name,
                                'total_held': 0,
                                'deals_count': 0
                            }
                        admin_stats[admin_id]['total_held'] += amount
                        admin_stats[admin_id]['deals_count'] += 1
            except (ValueError, AttributeError):
                continue
    finally:
        if conn:
            return_db_connection(conn)
    
    if not admin_stats:
        await update.message.reply_text("📭 No deals created today yet.")
        return
    
    # Sort by total held amount
    sorted_admins = sorted(admin_stats.items(), key=lambda x: x[1]['total_held'], reverse=True)
    
    message = f"📊 **Admin Volume Today ({today.strftime('%d %b %Y')})**\n\n"
    
    for admin_id, stats in sorted_admins:
        # Escape Markdown special characters in admin names
        admin_name_escaped = escape_markdown(stats['name'])
        message += f"**👤 {admin_name_escaped}: ${stats['total_held']:,.2f}**\n"
    
    total_held = sum(s['total_held'] for s in admin_stats.values())
    
    message += f"\n**💰 Total Today: ${total_held:,.2f}**"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ==========================
# /ADDSTAT COMMAND (Conversation)
# ==========================

ADDSTAT_VOLUME, ADDSTAT_DEALS, ADDSTAT_HIGHEST = range(3)

async def addstat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /addstat. Identifies the target user and asks for Total Volume."""
    user = update.message.from_user
    if user.id not in ADMINS:
        await update.message.reply_text("🚫 Only authorized admins can use this command.")
        return ConversationHandler.END

    target_username = None
    target_user_id = None

    # Option 1: Reply to a user's message
    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        target_username = f"@{replied_user.username}" if replied_user.username else None
        target_user_id = replied_user.id
        if not target_username:
            await update.message.reply_text("⚠️ The replied user has no username.")
            return ConversationHandler.END

    # Option 2: Argument provided (username or user ID)
    elif context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith('@'):
            target_username = arg
        elif arg.isdigit():
            target_user_id = int(arg)
            # Try to resolve username from user ID via Telethon
            if telethon_client:
                try:
                    entity = await telethon_client.get_entity(target_user_id)
                    if entity.username:
                        target_username = f"@{entity.username}"
                except Exception as e:
                    print(f"⚠️ Could not resolve user ID {arg}: {e}")
            if not target_username:
                target_username = f"ID:{arg}"
        else:
            target_username = f"@{arg}"
    else:
        await update.message.reply_text(
            "⚠️ Please specify a user.\n\n"
            "Usage:\n"
            "• /addstat @username\n"
            "• /addstat <user_id>\n"
            "• Reply to a user's message with /addstat"
        )
        return ConversationHandler.END

    # Store target info in context for later steps
    context.user_data['addstat_username'] = target_username
    context.user_data['addstat_user_id'] = target_user_id

    await update.message.reply_text(
        f"📊 Adding stats for {target_username}\n\n"
        f"Step 1/3: Enter Total Volume (in $):"
    )
    return ADDSTAT_VOLUME

async def addstat_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive Total Volume and ask for Completed Deals."""
    if await _try_process_full_stats_message(update, context):
        return ConversationHandler.END

    try:
        volume = float(update.message.text.strip().replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid Total Volume (e.g. 5000):")
        return ADDSTAT_VOLUME

    context.user_data['addstat_volume'] = volume
    await update.message.reply_text("Step 2/3: Enter Completed Deals (number):")
    return ADDSTAT_DEALS

async def addstat_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive Completed Deals and ask for Highest Deal."""
    if await _try_process_full_stats_message(update, context):
        return ConversationHandler.END

    try:
        deals = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid number of Completed Deals (e.g. 25):")
        return ADDSTAT_DEALS

    context.user_data['addstat_deals'] = deals
    await update.message.reply_text("Step 3/3: Enter Highest Deal (in $):")
    return ADDSTAT_HIGHEST

async def addstat_highest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive Highest Deal and save all stats."""
    if await _try_process_full_stats_message(update, context):
        return ConversationHandler.END

    try:
        highest = float(update.message.text.strip().replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid Highest Deal (e.g. 500):")
        return ADDSTAT_HIGHEST

    username = context.user_data.get('addstat_username')
    user_id = context.user_data.get('addstat_user_id')
    volume = context.user_data.get('addstat_volume')
    deals = context.user_data.get('addstat_deals')

    success, error_info = save_manual_stats(username, user_id, volume, deals, highest)

    if success:
        note = " (saved to local file - DB unavailable)" if error_info == "saved_to_json" else ""
        await update.message.reply_text(
            f"✅ Stats updated for {username}{note}\n\n"
            f"Total Volume: ${volume:,.2f}\n"
            f"Completed Deals: {deals}\n"
            f"Highest Deal: ${highest:,.2f}"
        )
    else:
        await update.message.reply_text(f"❌ Failed to save stats.\nError: {error_info}")

    # Clean up user_data
    for key in ['addstat_username', 'addstat_user_id', 'addstat_volume', 'addstat_deals']:
        context.user_data.pop(key, None)

    return ConversationHandler.END

async def addstat_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the /addstat conversation."""
    for key in ['addstat_username', 'addstat_user_id', 'addstat_volume', 'addstat_deals']:
        context.user_data.pop(key, None)
    await update.message.reply_text("❌ /addstat cancelled.")
    return ConversationHandler.END

# ==========================
# MAIN FUNCTION
# ==========================

def main():
    # Load deal history on startup (for backwards compatibility)
    load_history()
    
    # Load active deals from database
    load_active_deals_from_db()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation handler for /addstat (must be added before simple command handlers)
    addstat_conv = ConversationHandler(
        entry_points=[CommandHandler("addstat", addstat_start)],
        states={
            ADDSTAT_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addstat_volume)],
            ADDSTAT_DEALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, addstat_deals)],
            ADDSTAT_HIGHEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, addstat_highest)],
        },
        fallbacks=[CommandHandler("cancel", addstat_cancel)],
    )
    app.add_handler(addstat_conv)

    app.add_handler(CommandHandler("add", add_deal))
    app.add_handler(CallbackQueryHandler(fee_selected, pattern=r"^fee_"))
    app.add_handler(CommandHandler("close", close_deal))
    app.add_handler(CommandHandler("refund", refund_deal))
    app.add_handler(CommandHandler("active_deals", show_active_deals))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("adminwise", adminwise_command))

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    # Initialize Telethon before starting bot
    asyncio.run(initialize_telethon())
    main()
