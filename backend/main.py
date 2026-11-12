from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta, date
import sqlite3, csv, json, os, hashlib, hmac, secrets, io, uuid, base64, urllib.request, urllib.error, shutil
import jwt, qrcode
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
SOURCE_DB = BASE / "mealsync.db"
DB = Path(os.getenv("MEALSYNC_DB_PATH", str(SOURCE_DB)))
if not DB.is_absolute(): DB = BASE / DB
DB.parent.mkdir(parents=True, exist_ok=True)
if DB.resolve() != SOURCE_DB.resolve() and not DB.exists() and SOURCE_DB.exists():
    shutil.copy2(SOURCE_DB, DB)
SECRET = os.getenv("MEALSYNC_SECRET", "change-this-demo-secret-before-deployment")
if os.getenv("RENDER", "").lower() == "true" and SECRET == "change-this-demo-secret-before-deployment":
    raise RuntimeError("Set MEALSYNC_SECRET to a generated secret before deployment.")
app = FastAPI(title="MealSync API", version="1.1.0")
MEDIA_DIR = Path(os.getenv("MEALSYNC_MEDIA_DIR", str(BASE / "uploads")))
if not MEDIA_DIR.is_absolute(): MEDIA_DIR = BASE / MEDIA_DIR
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
configured_origins = os.getenv("MEALSYNC_CORS_ORIGINS", "").strip()
allowed_origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()] or ["http://localhost:5173","http://127.0.0.1:5173"]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def conn():
    c=sqlite3.connect(DB,timeout=8); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=8000"); return c
def hash_pw(pw, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+":"+hashlib.pbkdf2_hmac("sha256",pw.encode(),salt.encode(),180000).hex()
def check_pw(pw, stored):
    try:
        salt, digest=stored.split(":")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256",pw.encode(),salt.encode(),180000).hex(),digest)
    except: return False

def init_db():
    c=conn()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','vendor','admin')), shop_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS shops(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', opening_time TEXT DEFAULT '08:00', closing_time TEXT DEFAULT '18:00', is_open INTEGER DEFAULT 1, image_url TEXT DEFAULT '', owner_user_id INTEGER, FOREIGN KEY(owner_user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS foods(id TEXT PRIMARY KEY, shop_id TEXT NOT NULL, name TEXT NOT NULL, category TEXT DEFAULT '', description TEXT DEFAULT '', price_sen INTEGER NOT NULL, food_type TEXT DEFAULT 'menu', parent_food_id TEXT, available INTEGER DEFAULT 1, active INTEGER DEFAULT 1, top_selling INTEGER DEFAULT 0, todays_special INTEGER DEFAULT 0, image_url TEXT DEFAULT '', nutrition_json TEXT DEFAULT '{}', tags_json TEXT DEFAULT '[]', FOREIGN KEY(shop_id) REFERENCES shops(id));
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT UNIQUE NOT NULL, student_id INTEGER NOT NULL, shop_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', total_sen INTEGER NOT NULL, qr_token TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, collected_at TEXT, FOREIGN KEY(student_id) REFERENCES users(id), FOREIGN KEY(shop_id) REFERENCES shops(id));
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, food_id TEXT NOT NULL, food_name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price_sen INTEGER NOT NULL, addons_json TEXT DEFAULT '[]', notes TEXT DEFAULT '', FOREIGN KEY(order_id) REFERENCES orders(id), FOREIGN KEY(food_id) REFERENCES foods(id));
    CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, description TEXT NOT NULL, category TEXT DEFAULT 'Food', amount_sen INTEGER NOT NULL, spent_on TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS budgets(user_id INTEGER PRIMARY KEY, monthly_budget_sen INTEGER DEFAULT 0, FOREIGN KEY(user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS food_costs(food_id TEXT PRIMARY KEY, cost_sen INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, student_id INTEGER NOT NULL, shop_id TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), comment TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, vendor_reply TEXT DEFAULT '', FOREIGN KEY(order_id) REFERENCES orders(id), FOREIGN KEY(student_id) REFERENCES users(id), FOREIGN KEY(shop_id) REFERENCES shops(id));
    """)
    # Migrate legacy users table so the admin role is supported without losing existing accounts.
    user_sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0] or ""
    if "'admin'" not in user_sql:
        c.execute("PRAGMA foreign_keys=OFF")
        c.executescript("""
        CREATE TABLE users_new(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('student','vendor','admin')),
          shop_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          is_active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO users_new(id,name,email,password_hash,role,shop_id,created_at,is_active)
          SELECT id,name,email,password_hash,role,shop_id,created_at,1 FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        """)
        c.execute("PRAGMA foreign_keys=ON")
    user_cols={r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "is_active" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    # Safe schema migration for databases created by earlier MealSync builds.
    cols={r[1] for r in c.execute("PRAGMA table_info(foods)").fetchall()}
    if "tags_json" not in cols: c.execute("ALTER TABLE foods ADD COLUMN tags_json TEXT DEFAULT '[]'")
    order_cols={r[1] for r in c.execute("PRAGMA table_info(orders)").fetchall()}
    if "payment_method" not in order_cols: c.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'APay'")
    if "reject_reason" not in order_cols: c.execute("ALTER TABLE orders ADD COLUMN reject_reason TEXT DEFAULT ''")
    if "prep_minutes" not in order_cols: c.execute("ALTER TABLE orders ADD COLUMN prep_minutes INTEGER DEFAULT 10")
    shop_cols={r[1] for r in c.execute("PRAGMA table_info(shops)").fetchall()}
    if "weekly_hours_json" not in shop_cols: c.execute("ALTER TABLE shops ADD COLUMN weekly_hours_json TEXT DEFAULT '{}'")
    food_cols={r[1] for r in c.execute("PRAGMA table_info(foods)").fetchall()}
    if "lunch_set_enabled" not in food_cols:
        c.execute("ALTER TABLE foods ADD COLUMN lunch_set_enabled INTEGER DEFAULT 0")
        c.execute("UPDATE foods SET lunch_set_enabled=1 WHERE food_type='tray'")
    c.execute("UPDATE foods SET lunch_set_enabled=0 WHERE shop_id NOT IN ('SHOP001','SHOP005')")
    if "temporary_closed_until" not in shop_cols: c.execute("ALTER TABLE shops ADD COLUMN temporary_closed_until TEXT DEFAULT ''")
    # Query indexes keep the vendor kitchen queue responsive as order history grows.
    c.executescript("""
    CREATE INDEX IF NOT EXISTS idx_orders_shop_status_created ON orders(shop_id,status,created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_orders_student_created ON orders(student_id,created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_reviews_order ON reviews(order_id);
    CREATE INDEX IF NOT EXISTS idx_foods_shop_active ON foods(shop_id,active,food_type);
    """)
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0:
        c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",("Demo Student","student@mealsync.test",hash_pw("student123"),"student"))
        c.execute("INSERT INTO users(name,email,password_hash,role,shop_id) VALUES(?,?,?,?,?)",("Demo Vendor","vendor@mealsync.test",hash_pw("vendor123"),"vendor","SHOP001"))
    if c.execute("SELECT COUNT(*) FROM shops").fetchone()[0]==0:
        rows=list(csv.DictReader(open(BASE/"food_seed.csv",encoding="utf-8-sig")))
        seen={}
        for r in rows:
            sid=r["shop_id"]
            if sid not in seen:
                seen[sid]=r["shop_name"]
                c.execute("INSERT INTO shops(id,name,description,image_url) VALUES(?,?,?,?)",(sid,r["shop_name"],r["shop_name"]+" campus food and favourites.",""))
        for r in rows:
            price=int(round(float(r["price_myr"] or 0)*100))
            c.execute("""INSERT OR IGNORE INTO foods(id,shop_id,name,category,description,price_sen,food_type,parent_food_id,available,active,top_selling,todays_special,image_url)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(r["food_id"],r["shop_id"],r["food_name"],r["category"],r["description"],price,r["type_of_food"],r.get("parent_food_id") or None,int(r["is_available"].lower()=="true"),int(r["is_active"].lower()=="true"),int(r["is_top_selling"].lower()=="true"),int(r["is_todays_special"].lower()=="true"),""))
    # One distinct demo vendor login per shop. Each account is scoped to exactly one shop.
    vendor_accounts={
        "SHOP001":("Chinese Vendor","chinese@mealsync.test"),
        "SHOP002":("Fast Food Vendor","fastfood@mealsync.test"),
        "SHOP003":("Kopitiam Vendor","kopitiam@mealsync.test"),
        "SHOP004":("Japanese Korean Vendor","japanese@mealsync.test"),
        "SHOP005":("Mamak Vendor","mamak@mealsync.test"),
        "SHOP006":("Shawarma Vendor","shawarma@mealsync.test"),
        "SHOP007":("Asian Cuisine Vendor","asian@mealsync.test"),
        "SHOP008":("Picante Vendor","picante@mealsync.test")
    }
    for shop_id,(vendor_name,vendor_email) in vendor_accounts.items():
        if c.execute("SELECT 1 FROM shops WHERE id=?",(shop_id,)).fetchone():
            existing=c.execute("SELECT id FROM users WHERE email=?",(vendor_email,)).fetchone()
            if existing:
                c.execute("UPDATE users SET shop_id=?,role='vendor',is_active=1 WHERE id=?",(shop_id,existing["id"]))
                vendor_id=existing["id"]
            else:
                cur=c.execute("INSERT INTO users(name,email,password_hash,role,shop_id,is_active) VALUES(?,?,?,?,?,1)",(vendor_name,vendor_email,hash_pw("vendor123"),"vendor",shop_id))
                vendor_id=cur.lastrowid
            c.execute("UPDATE shops SET owner_user_id=? WHERE id=?",(vendor_id,shop_id))
    # One demo platform administrator for hackathon evaluation.
    admin_email=os.getenv("MEALSYNC_ADMIN_EMAIL","admin@mealsync.test").strip().lower()
    configured_admin_password=os.getenv("MEALSYNC_ADMIN_PASSWORD","").strip()
    if os.getenv("RENDER", "").lower() == "true" and not configured_admin_password:
        raise RuntimeError("Set MEALSYNC_ADMIN_PASSWORD to a private generated password before deployment.")
    admin_password=configured_admin_password or "admin123"
    admin=c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not admin:
        c.execute("INSERT OR IGNORE INTO users(name,email,password_hash,role,is_active) VALUES(?,?,?,?,1)",
                  ("MealSync Administrator",admin_email,hash_pw(admin_password),"admin"))
    elif configured_admin_password:
        c.execute("UPDATE users SET email=?,password_hash=?,is_active=1 WHERE id=?",
                  (admin_email,hash_pw(configured_admin_password),admin["id"]))
    c.commit()
    if os.getenv("MEALSYNC_SEED_DEMO_DATA", "true").strip().lower() in {"0", "false", "no"}:
        c.close()
        return
    # Clearly labelled demo history so the prototype screens are populated.
    student=c.execute("SELECT id FROM users WHERE email='student@mealsync.test'").fetchone()
    if student and c.execute("SELECT COUNT(*) FROM orders WHERE reference LIKE 'MSDEMO%'").fetchone()[0]==0:
        sid=student["id"]
        foods=c.execute("SELECT id,name,shop_id,price_sen FROM foods WHERE food_type='menu' AND active=1 ORDER BY id LIMIT 3").fetchall()
        if foods:
            for ix,status in enumerate(["collected","ready","preparing"]):
                f=foods[min(ix,len(foods)-1)]
                ref=f"MSDEMO{ix+1}"
                tok=secrets.token_urlsafe(24)
                cur=c.execute("INSERT INTO orders(reference,student_id,shop_id,status,total_sen,qr_token) VALUES(?,?,?,?,?,?)",(ref,sid,f["shop_id"],status,f["price_sen"],tok))
                c.execute("INSERT INTO order_items(order_id,food_id,food_name,quantity,unit_price_sen) VALUES(?,?,?,?,?)",(cur.lastrowid,f["id"],f["name"],1,f["price_sen"]))
            c.execute("INSERT OR IGNORE INTO budgets(user_id,monthly_budget_sen) VALUES(?,?)",(sid,40000))
    # Eight shops receive a 90-day synthetic analytics history with naturally varying
    # daily demand (roughly 112-178 orders/day). Idempotent references make reseeding safe.
    import random as _random
    shop_rows=c.execute("SELECT id,name FROM shops ORDER BY id").fetchall()
    for shop_row in shop_rows:
        shop_id=shop_row["id"]
        demo_student_ids=[]
        demo_password_hash=hash_pw("demo-only-disabled-login")
        for customer_no in range(1,2501):
            demo_email=f"analytics+{shop_id.lower()}+{customer_no:03d}@mealsync.test"
            demo_user=c.execute("SELECT id FROM users WHERE email=?",(demo_email,)).fetchone()
            if not demo_user:
                c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",(f"{shop_row['name']} Demo Customer {customer_no:03d}",demo_email,demo_password_hash))
                demo_user=c.execute("SELECT id FROM users WHERE email=?",(demo_email,)).fetchone()
            demo_student_ids.append(demo_user["id"])
        shop_foods=c.execute("SELECT id,name,price_sen FROM foods WHERE shop_id=? AND active=1 AND available=1 AND food_type!='addons' ORDER BY id",(shop_id,)).fetchall()
        if not shop_foods: continue
        for days_ago in range(90):
            day=(date.today()-timedelta(days=days_ago)).isoformat()
            rng=_random.Random(f"{shop_id}-{day}-demand")
            target_count=rng.randint(112,178)
            prefix=f"MSANALYTICS-{shop_id}-{day}-"
            existing_rows=c.execute("SELECT reference,status FROM orders WHERE reference LIKE ?",(prefix+'%',)).fetchall()
            existing_nums={int(row["reference"].rsplit('-',1)[-1]):row for row in existing_rows}
            # Vary historical daily activity without deleting order records.
            for seq,row in existing_nums.items():
                desired_status="collected" if seq<=target_count else "cancelled"
                if row["status"] in ("collected","cancelled") and row["status"]!=desired_status:
                    c.execute("UPDATE orders SET status=? WHERE reference=?",(desired_status,row["reference"]))
            for seq in range(1,target_count+1):
                if seq in existing_nums: continue
                order_rng=_random.Random(f"{shop_id}-{day}-{seq}")
                food=shop_foods[order_rng.randrange(len(shop_foods))]
                qty=2 if order_rng.random()<0.22 else 1
                total=int(food["price_sen"])*qty
                hour=order_rng.choices([8,9,10,11,12,13,14,15,16,17,18],[3,5,7,15,20,17,10,6,5,7,5])[0]
                created=f"{day} {hour:02d}:{order_rng.randrange(60):02d}:{order_rng.randrange(60):02d}"
                ref=f"{prefix}{seq:03d}"
                token=secrets.token_urlsafe(24)
                student_id=demo_student_ids[order_rng.randrange(len(demo_student_ids))]
                cur=c.execute("INSERT INTO orders(reference,student_id,shop_id,status,total_sen,qr_token,created_at,payment_method,prep_minutes) VALUES(?,?,?,?,?,?,?,?,?)",(ref,student_id,shop_id,"collected",total,token,created,order_rng.choice(["APay","APCard"]),order_rng.choice([8,10,12,15,18])))
                c.execute("INSERT INTO order_items(order_id,food_id,food_name,quantity,unit_price_sen) VALUES(?,?,?,?,?)",(cur.lastrowid,food["id"],food["name"],qty,food["price_sen"]))
        # Seed a small, realistic live kitchen queue for every shop. These records are
        # idempotent and remain active until a vendor processes them in the demo.
        live_prefix=f"MSLIVE-{shop_id}-{date.today().isoformat()}-"
        live_existing={r[0] for r in c.execute("SELECT reference FROM orders WHERE reference LIKE ?",(live_prefix+'%',)).fetchall()}
        live_statuses=["pending","pending","pending","preparing","preparing","ready"]
        for seq,status_value in enumerate(live_statuses,1):
            ref=f"{live_prefix}{seq:02d}"
            if ref in live_existing: continue
            order_rng=_random.Random(f"live-{shop_id}-{date.today().isoformat()}-{seq}")
            food=shop_foods[order_rng.randrange(len(shop_foods))]
            qty=2 if order_rng.random()<0.25 else 1
            total=int(food["price_sen"])*qty
            age_minutes={1:3,2:6,3:9,4:7,5:13,6:2}[seq]
            created=(datetime.now()-timedelta(minutes=age_minutes)).strftime("%Y-%m-%d %H:%M:%S")
            student_id=demo_student_ids[order_rng.randrange(len(demo_student_ids))]
            cur=c.execute("INSERT INTO orders(reference,student_id,shop_id,status,total_sen,qr_token,created_at,payment_method,prep_minutes) VALUES(?,?,?,?,?,?,?,?,?)",(ref,student_id,shop_id,status_value,total,secrets.token_urlsafe(24),created,order_rng.choice(["APay","APCard"]),order_rng.choice([8,10,12,15])))
            c.execute("INSERT INTO order_items(order_id,food_id,food_name,quantity,unit_price_sen) VALUES(?,?,?,?,?)",(cur.lastrowid,food["id"],food["name"],qty,food["price_sen"]))
    c.commit()
    # Rich demo feedback across every shop, attached to real-looking collected demo orders.
    # Idempotent: only creates reviews for the deterministic analytics order references.
    import random as _feedback_random
    feedback_comments={
        5:["Fresh, tasty and served at the right temperature.","Really good portion for the price. Will order again!","Smooth pickup and the food was delicious.","One of my campus favourites now."],
        4:["Good flavour and decent portion. Pickup was quick.","Enjoyed it. Would be even better with a little more sauce.","Tasty meal and convenient ordering experience.","Good value overall; food was still warm."],
        3:["It was okay, but the portion could be more generous.","Average today. The pickup process was smooth.","Flavour was decent; hope consistency improves."],
        2:["Food arrived cooler than expected. Please improve timing.","The taste was okay, but the order took longer than expected."],
        1:["My order did not meet expectations today. Please follow up."]
    }
    for shop_row in c.execute("SELECT id,name FROM shops ORDER BY id").fetchall():
        sid_feedback=shop_row["id"]
        refs=c.execute("SELECT id,student_id FROM orders WHERE shop_id=? AND reference LIKE 'MSANALYTICS-%' AND status='collected' ORDER BY created_at DESC LIMIT 60",(sid_feedback,)).fetchall()
        for ix,order_row in enumerate(refs):
            if c.execute("SELECT 1 FROM reviews WHERE order_id=?",(order_row["id"],)).fetchone(): continue
            rng=_feedback_random.Random(f"review-{sid_feedback}-{order_row['id']}")
            rating=rng.choices([5,4,3,2,1],[35,38,18,7,2])[0]
            comment=rng.choice(feedback_comments[rating])
            created=(datetime.now()-timedelta(days=ix%57,hours=rng.randrange(1,12))).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO reviews(order_id,student_id,shop_id,rating,comment,created_at) VALUES(?,?,?,?,?,?)",(order_row["id"],order_row["student_id"],sid_feedback,rating,comment,created))

    # Demo student's food spending: two purchases per day averaging RM20–RM30/day.
    # Synthetic orders are clearly labelled by reference and isolated to the demo student.
    if student:
        demo_sid=student["id"]
        spend_foods=c.execute("SELECT id,name,shop_id,price_sen FROM foods WHERE active=1 AND available=1 AND food_type='menu' AND price_sen BETWEEN 500 AND 1500 ORDER BY id").fetchall()
        if spend_foods:
            import random as _spend_random
            for days_ago in range(30):
                day=(date.today()-timedelta(days=days_ago)).isoformat()
                for meal_no in (1,2):
                    ref=f"MSSTUDENTSPEND-{day}-{meal_no}"
                    if c.execute("SELECT 1 FROM orders WHERE reference=?",(ref,)).fetchone(): continue
                    rng=_spend_random.Random(f"student-spend-{day}-{meal_no}")
                    food=spend_foods[rng.randrange(len(spend_foods))]
                    qty=1 + (1 if rng.random()<0.22 else 0)
                    total=int(food["price_sen"])*qty
                    hour=rng.choice([9,11,12,13,15,18,19])
                    created=f"{day} {hour:02d}:{rng.randrange(60):02d}:00"
                    cur=c.execute("INSERT INTO orders(reference,student_id,shop_id,status,total_sen,qr_token,created_at,payment_method) VALUES(?,?,?,?,?,?,?,?)",(ref,demo_sid,food["shop_id"],"collected",total,secrets.token_urlsafe(24),created,rng.choice(["APay","APCard"])))
                    c.execute("INSERT INTO order_items(order_id,food_id,food_name,quantity,unit_price_sen) VALUES(?,?,?,?,?)",(cur.lastrowid,food["id"],food["name"],qty,food["price_sen"]))
                # Keep each synthetic day within the stated RM20–RM30 food-spend band.
                day_rows=c.execute("SELECT o.id,o.total_sen,oi.id item_id,oi.unit_price_sen FROM orders o JOIN order_items oi ON oi.order_id=o.id WHERE o.student_id=? AND o.reference LIKE ? ORDER BY o.reference",(demo_sid,f"MSSTUDENTSPEND-{day}-%")).fetchall()
                if len(day_rows)>=2:
                    import itertools as _itertools
                    choices=[]
                    for q1,q2 in _itertools.product(range(1,4),repeat=2):
                        total=day_rows[0]["unit_price_sen"]*q1+day_rows[1]["unit_price_sen"]*q2
                        if 2000<=total<=3000: choices.append((abs(total-2450),total,q1,q2))
                    if choices:
                        _,target,q1,q2=min(choices)
                        for row,qty_new in zip(day_rows,(q1,q2)):
                            c.execute("UPDATE orders SET total_sen=? WHERE id=?",(row["unit_price_sen"]*qty_new,row["id"]))
                            c.execute("UPDATE order_items SET quantity=? WHERE id=?",(qty_new,row["item_id"]))

    # Refresh item-specific add-ons for the demo menu on every startup.
    # Each food receives a relevant set; matching extras may be reused across similar items.
    import re as _re
    def addon_pack(name, category):
        t=(name+" "+category).lower()
        if "teh tarik" in t: return [("Extra tea kaw","Stronger pulled-tea flavour",180),("Less sweet","Reduce condensed milk",0),("Extra foam","A thicker frothy top",80),("Cincau","Add grass jelly",150)]
        if "kopi o" in t: return [("Kopi kaw","A stronger black coffee",150),("Less sweet","Reduce sugar",0),("Lemon slice","A bright citrus twist",80),("Extra ice","Serve it colder",0)]
        if "milo" in t: return [("Milo powder","Richer chocolate malt flavour",150),("Milo dinosaur topping","Extra malt powder on top",200),("Less sweet","Reduce condensed milk",0),("Extra milk","Creamier finish",120)]
        if any(k in t for k in ["limau ais","lemonade","lemon tea"]): return [("Extra citrus","A brighter lime or lemon hit",100),("Honey","Natural sweetness",120),("Mint leaves","Fresh mint aroma",80),("Less sweet","Reduce syrup",0)]
        if any(k in t for k in ["coffee","tea","latte","milo","juice","smoothie","drink","beverage","water","matcha","milkshake","minuman","ais","teh","kopi"]):
            if any(k in t for k in ["coffee","latte","espresso"]):
                return [("Extra espresso shot","A stronger coffee finish",250),("Oat milk","Creamy dairy-free alternative",300),("Vanilla syrup","A touch of sweetness",150),("Caramel drizzle","Sweet caramel finish",180)]
            if any(k in t for k in ["tea","matcha"]):
                return [("Less ice","Keep the flavour full",0),("Extra tea shot","A deeper tea flavour",200),("Honey","Natural sweetness",120),("Oat milk","Smooth and creamy",300)]
            if any(k in t for k in ["juice","smoothie"]):
                return [("Less sugar","A lighter finish",0),("Chia seeds","Add texture",150),("Extra fruit","More fruity flavour",250),("Yoghurt","Creamier blend",200)]
            return [("Less ice","More drink, less ice",0),("Less sugar","A lighter finish",0),("Extra shot","Boost the flavour",150),("Whipped cream","Creamy topping",180)]
        if "char siew" in t: return [("Extra char siew","More caramelised BBQ pork",450),("Char siew sauce","Extra house glaze",100),("Pickled vegetables","Add a tangy crunch",100),("Wonton soup","A warm side serving",250)]
        if "siew yuk" in t: return [("Extra roast pork","More crispy pork belly",450),("Crispy skin boost","Extra crackling topping",180),("Plum sauce","Sweet and tangy dip",100),("Pickled vegetables","Add a tangy crunch",100)]
        if "chicken rice" in t: return [("Extra chicken","More poached or roasted chicken",400),("Ginger-scallion oil","Classic fragrant sauce",100),("Dark soy drizzle","Sweet savoury finish",80),("Chicken broth","A warm side soup",150)]
        if "wantan mee" in t: return [("Extra wantan","Add more pork or prawn dumplings",250),("Char siew slices","Add BBQ pork",300),("Extra egg noodles","A bigger noodle portion",200),("Chilli paste","More heat",80)]
        if any(k in t for k in ["burger","hot dog","wrap","sandwich"]):
            return [("Extra cheese","Melted cheese layer",250),("Add beef patty","Make it more filling",450),("Crispy bacon","Smoky crunchy topping",300),("Signature sauce","House sauce on the side",100)]
        if any(k in t for k in ["fries","nugget","popcorn","onion ring","fried chicken"]):
            return [("Cheese sauce","Warm cheesy dip",180),("Spicy seasoning","Extra heat and flavour",80),("BBQ dip","Smoky dipping sauce",100),("Extra portion","A little more to share",250)]
        if any(k in t for k in ["noodle","mee","mi ","kuey teow","pasta"]):
            return [("Extra noodles","A more filling serving",250),("Add egg","Soft or fried egg",180),("Extra protein","More meat or tofu",350),("Chilli oil","A spicy aromatic finish",80)]
        if any(k in t for k in ["rice","nasi","biryani","don","curry"]):
            return [("Extra rice","A larger rice portion",150),("Fried egg","Crispy-edged egg",180),("Extra gravy","More sauce for your rice",100),("Sambal","Spicy house sambal",80)]
        if any(k in t for k in ["fish","prawn","shrimp","squid","seafood","ikan","udang","sotong"]):
            return [("Lemon butter sauce","Bright buttery finish",180),("Extra seafood","Add more seafood",400),("Garlic butter","Rich garlic flavour",180),("Chilli dip","A spicy side dip",80)]
        if any(k in t for k in ["chicken","ayam"]):
            return [("Extra chicken","Add another chicken portion",400),("Black pepper sauce","Peppery savoury sauce",120),("Garlic sauce","Rich garlic finish",120),("Fried egg","Crispy-edged egg",180)]
        if any(k in t for k in ["beef","steak","lamb","mutton","kambing"]):
            return [("Extra meat","A more generous portion",450),("Mushroom sauce","Creamy mushroom topping",180),("Caramelised onion","Sweet savoury topping",120),("Fried egg","Crispy-edged egg",180)]
        return [("Extra vegetables","Fresh vegetable topping",150),("Tofu","Add a protein option",200),("Chilli sauce","A spicy finish",80),("Sesame dressing","Nutty savoury dressing",120)]
    for food in c.execute("SELECT id,name,category FROM foods WHERE food_type='menu' AND active=1").fetchall():
        pack=addon_pack(food["name"],food["category"] or "")
        existing=c.execute("SELECT id FROM foods WHERE parent_food_id=? AND food_type='addons'",(food["id"],)).fetchall()
        for row in existing: c.execute("DELETE FROM foods WHERE id=?",(row["id"],))
        for ix,(nm,desc,price) in enumerate(pack,1):
            aid=f"{food['id']}-X{ix:02d}"
            c.execute("""INSERT INTO foods(id,shop_id,name,category,description,price_sen,food_type,parent_food_id,available,active)
                         SELECT ?,shop_id,?,'Add-on',?,?, 'addons',?,1,1 FROM foods WHERE id=?""",
                      (aid,nm,desc,price,food["id"],food["id"]))
    c.commit(); c.close()
init_db()

class Login(BaseModel):
    email:str
    password:str
class OrderItem(BaseModel):
    food_id:str
    quantity:int=Field(ge=1,le=30)
    addon_ids:List[str]=[]
    tray_food_ids:List[str]=[]
    notes:str=""
class OrderCreate(BaseModel):
    shop_id:str
    payment_method:str="APay"
    items:List[OrderItem]
class FoodUpdate(BaseModel):
    name:Optional[str]=None
    description:Optional[str]=None
    category:Optional[str]=None
    price_sen:Optional[int]=None
    available:Optional[bool]=None
    active:Optional[bool]=None
    todays_special:Optional[bool]=None
    image_url:Optional[str]=None
    nutrition_json:Optional[str]=None
    lunch_set_enabled:Optional[bool]=None
class ExpenseCreate(BaseModel):
    description:str
    category:str="Food"
    amount_sen:int=Field(gt=0)
    spent_on:str
class BudgetUpdate(BaseModel):
    monthly_budget_sen:int=Field(ge=0)
class StatusUpdate(BaseModel):
    status:str
    reject_reason:str=""
    prep_minutes:int=Field(default=10,ge=1,le=180)
class FoodCreate(BaseModel):
    id:Optional[str]=None
    name:str
    category:str=""
    description:str=""
    price_sen:int=Field(ge=0)
    food_type:str="menu"
    image_url:str=""
    available:bool=True
    nutrition:dict={}
    tags:List[str]=[]
    cost_sen:int=Field(default=0,ge=0)
    lunch_set_enabled:bool=False
class ShopUpdate(BaseModel):
    is_open:Optional[bool]=None
    opening_time:Optional[str]=None
    closing_time:Optional[str]=None
    description:Optional[str]=None
    temporary_closed_until:Optional[str]=None
class CostUpdate(BaseModel):
    cost_sen:int=Field(ge=0)
class ReviewReply(BaseModel):
    reply:str=Field(min_length=1,max_length=1000)

def current_user(authorization: str=Header(default="")):
    if not authorization.startswith("Bearer "): raise HTTPException(401,"Sign in required")
    try: payload=jwt.decode(authorization[7:],SECRET,algorithms=["HS256"])
    except: raise HTTPException(401,"Session expired")
    c=conn(); u=c.execute("SELECT id,name,email,role,shop_id,is_active FROM users WHERE id=?",(payload["uid"],)).fetchone(); c.close()
    if not u or not u["is_active"]: raise HTTPException(401,"Account not found or disabled")
    return dict(u)
def vendor_shop(user):
    if user["role"]!="vendor": raise HTTPException(403,"Vendor access only")
    if not user.get("shop_id"): raise HTTPException(403,"This vendor account is not assigned to a shop")
    return user["shop_id"]
def money(v): return int(v or 0)

def infer_tags(food):
    text=(str(food.get("name", ""))+" "+str(food.get("category", ""))+" "+str(food.get("description", ""))).lower()
    tags=[]
    rules=[("chicken",["chicken","ayam"]),("fish",["fish","ikan"]),("beef",["beef","lembu"]),("mutton",["mutton","lamb","kambing"]),("seafood",["prawn","shrimp","udang","squid","sotong","crab","ketam","seafood"]),("vegetarian",["vegetarian","vegetable","veggie","sayur","tofu","mushroom","paneer"]),("beverages",["beverage","beverages","tea","coffee","juice","drink","ais","milo","water","latte","minuman"]),("western",["burger","fries","pasta","steak","western"]),("asian",["rice","nasi","noodle","mee","mi","curry","kari","asian","japanese","korean"])]
    for tag,words in rules:
        if any(w in text for w in words): tags.append(tag)
    if "fish" in tags: tags.append("seafood")
    if any(w in text for w in ["chicken","fish","beef","mutton","prawn","shrimp","squid","crab"]): tags.append("non-veg")
    elif "vegetarian" in tags: tags.append("veg")
    return list(dict.fromkeys(tags))

def enrich_food(row):
    d=dict(row)
    try: d["tags"]=json.loads(d.get("tags_json") or "[]")
    except: d["tags"]=[]
    d["tags"]=list(dict.fromkeys([*d["tags"],*infer_tags(d)]))
    try: n=json.loads(d.get("nutrition_json") or "{}")
    except: n={}
    if not n:
        # Demo estimates only; not verified vendor nutrition data.
        name=(d.get("name") or "").lower()
        base={"calories":520,"protein_g":24,"carbs_g":58,"fat_g":19}
        if "drink" in d.get("category", "").lower() or any(x in name for x in ["tea","coffee","juice","milo","water"]): base={"calories":110,"protein_g":3,"carbs_g":20,"fat_g":2}
        if "chicken" in name: base={"calories":560,"protein_g":34,"carbs_g":55,"fat_g":18}
        if "fish" in name: base={"calories":490,"protein_g":30,"carbs_g":52,"fat_g":14}
        if "beef" in name: base={"calories":610,"protein_g":32,"carbs_g":60,"fat_g":22}
        n={**base,"source":"Estimated demo values — not verified"}
    d["nutrition"]=n
    return d

@app.get("/api/health")
def health(): return {"status":"ok","service":"MealSync API"}
@app.post("/api/auth/login")
def login(body:Login):
    c=conn(); u=c.execute("SELECT * FROM users WHERE lower(email)=lower(?)",(body.email,)).fetchone(); c.close()
    if not u or not u["is_active"] or not check_pw(body.password,u["password_hash"]): raise HTTPException(401,"Incorrect email or password")
    token=jwt.encode({"uid":u["id"],"exp":datetime.utcnow()+timedelta(hours=12)},SECRET,algorithm="HS256")
    return {"token":token,"user":{"id":u["id"],"name":u["name"],"email":u["email"],"role":u["role"],"shop_id":u["shop_id"]}}
@app.get("/api/auth/me")
def me(user=Depends(current_user)): return user


def require_admin(user):
    if user.get("role")!="admin": raise HTTPException(403,"Administrator access only")
    return user

@app.get("/api/admin/overview")
def admin_overview(user=Depends(current_user)):
    require_admin(user)
    c=conn()
    counts={
      "students":c.execute("SELECT COUNT(*) FROM users WHERE role='student' AND is_active=1").fetchone()[0],
      "vendors":c.execute("SELECT COUNT(*) FROM users WHERE role='vendor' AND is_active=1").fetchone()[0],
      "inactive_vendors":c.execute("SELECT COUNT(*) FROM users WHERE role='vendor' AND is_active=0").fetchone()[0],
      "shops":c.execute("SELECT COUNT(*) FROM shops").fetchone()[0],
      "orders":c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
      "active_orders":c.execute("SELECT COUNT(*) FROM orders WHERE status NOT IN ('collected','cancelled')").fetchone()[0],
      "orders_today":c.execute("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now','localtime')").fetchone()[0],
      "revenue_sen":c.execute("SELECT COALESCE(SUM(total_sen),0) FROM orders WHERE status!='cancelled'").fetchone()[0]
    }
    # The overview chart is intentionally scoped to today's orders.
    # The database also contains 90 days of synthetic analytics history; plotting
    # every historical row here made the live operations chart misleadingly huge.
    status=[dict(r) for r in c.execute("""SELECT status,COUNT(*) count FROM orders
      WHERE date(created_at)=date('now','localtime')
      GROUP BY status ORDER BY count DESC""").fetchall()]
    recent=[dict(r) for r in c.execute("""SELECT o.id,o.reference,o.status,o.total_sen,o.created_at,
      s.name shop_name,u.name student_name FROM orders o JOIN shops s ON s.id=o.shop_id
      JOIN users u ON u.id=o.student_id ORDER BY o.created_at DESC LIMIT 10""").fetchall()]
    shops=[dict(r) for r in c.execute("""SELECT s.id,s.name,s.is_open,s.description,
      u.id vendor_id,u.name vendor_name,u.email vendor_email,u.is_active vendor_active,
      (SELECT COUNT(*) FROM foods f WHERE f.shop_id=s.id AND f.food_type!='addons' AND f.active=1) menu_items
      FROM shops s LEFT JOIN users u ON u.id=s.owner_user_id AND u.role='vendor'
      ORDER BY s.name""").fetchall()]
    c.close()
    return {"counts":counts,"order_status":status,"recent_orders":recent,"shops":shops}

@app.get("/api/admin/vendors")
def admin_vendors(user=Depends(current_user)):
    require_admin(user)
    c=conn()
    rows=[dict(r) for r in c.execute("""SELECT u.id,u.name,u.email,u.shop_id,u.is_active,u.created_at,
      s.name shop_name,s.is_open FROM users u LEFT JOIN shops s ON s.id=u.shop_id
      WHERE u.role='vendor' ORDER BY u.is_active DESC,s.name,u.name""").fetchall()]
    c.close()
    return rows

@app.post("/api/admin/vendors")
def admin_create_vendor(payload:dict,user=Depends(current_user)):
    require_admin(user)
    name=str(payload.get("name","")).strip()
    email=str(payload.get("email","")).strip().lower()
    password=str(payload.get("password",""))
    shop_id=str(payload.get("shop_id","")).strip()
    shop_name=str(payload.get("shop_name","")).strip()
    shop_description=str(payload.get("shop_description","")).strip()
    if not name or len(name)>100: raise HTTPException(400,"Enter a valid vendor name")
    if not email or "@" not in email or len(email)>180: raise HTTPException(400,"Enter a valid email address")
    if len(password)<8: raise HTTPException(400,"Password must contain at least 8 characters")
    c=conn()
    if c.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)",(email,)).fetchone():
        c.close(); raise HTTPException(409,"An account with this email already exists")
    if shop_id:
        shop=c.execute("SELECT id,name FROM shops WHERE id=?",(shop_id,)).fetchone()
        if not shop: c.close(); raise HTTPException(404,"Selected shop not found")
        occupied=c.execute("SELECT 1 FROM users WHERE role='vendor' AND shop_id=? AND is_active=1",(shop_id,)).fetchone()
        if occupied: c.close(); raise HTTPException(409,"This shop already has an active vendor. Deactivate the current vendor first.")
        resolved_shop_id=shop_id
    else:
        if not shop_name or len(shop_name)>120: c.close(); raise HTTPException(400,"Enter a shop name")
        resolved_shop_id="SHOP"+secrets.token_hex(4).upper()
        while c.execute("SELECT 1 FROM shops WHERE id=?",(resolved_shop_id,)).fetchone():
            resolved_shop_id="SHOP"+secrets.token_hex(4).upper()
        c.execute("INSERT INTO shops(id,name,description,is_open) VALUES(?,?,?,0)",
                  (resolved_shop_id,shop_name,shop_description))
    cur=c.execute("INSERT INTO users(name,email,password_hash,role,shop_id,is_active) VALUES(?,?,?,'vendor',?,1)",
                  (name,email,hash_pw(password),resolved_shop_id))
    vendor_id=cur.lastrowid
    c.execute("UPDATE shops SET owner_user_id=?,is_open=0 WHERE id=?",(vendor_id,resolved_shop_id))
    c.commit()
    row=dict(c.execute("""SELECT u.id,u.name,u.email,u.shop_id,u.is_active,u.created_at,s.name shop_name,s.is_open
      FROM users u JOIN shops s ON s.id=u.shop_id WHERE u.id=?""",(vendor_id,)).fetchone())
    c.close()
    return row

@app.delete("/api/admin/vendors/{vendor_id}")
def admin_deactivate_vendor(vendor_id:int,user=Depends(current_user)):
    require_admin(user)
    c=conn()
    vendor=c.execute("SELECT id,shop_id FROM users WHERE id=? AND role='vendor'",(vendor_id,)).fetchone()
    if not vendor: c.close(); raise HTTPException(404,"Vendor account not found")
    c.execute("UPDATE users SET is_active=0 WHERE id=?",(vendor_id,))
    c.execute("UPDATE shops SET is_open=0,owner_user_id=NULL WHERE id=? AND owner_user_id=?",(vendor["shop_id"],vendor_id))
    c.commit(); c.close()
    return {"ok":True,"message":"Vendor access disabled and shop closed. Order and menu history were retained."}

@app.post("/api/admin/vendors/{vendor_id}/restore")
def admin_restore_vendor(vendor_id:int,user=Depends(current_user)):
    require_admin(user)
    c=conn()
    vendor=c.execute("SELECT id,shop_id FROM users WHERE id=? AND role='vendor'",(vendor_id,)).fetchone()
    if not vendor: c.close(); raise HTTPException(404,"Vendor account not found")
    occupied=c.execute("SELECT 1 FROM users WHERE role='vendor' AND shop_id=? AND is_active=1 AND id!=?",(vendor["shop_id"],vendor_id)).fetchone()
    if occupied: c.close(); raise HTTPException(409,"Another active vendor is already assigned to this shop")
    c.execute("UPDATE users SET is_active=1 WHERE id=?",(vendor_id,))
    c.execute("UPDATE shops SET owner_user_id=? WHERE id=?",(vendor_id,vendor["shop_id"]))
    c.commit(); c.close()
    return {"ok":True,"message":"Vendor account restored. Shop remains closed until opened by the vendor."}

@app.get("/api/shops")
def shops(q:str=""):
    c=conn()
    sql="SELECT s.*, COUNT(f.id) food_count FROM shops s LEFT JOIN foods f ON f.shop_id=s.id AND f.food_type!='addons' AND f.active=1"
    args=[]
    if q: sql+=" WHERE lower(s.name) LIKE ? OR lower(s.description) LIKE ?"; args=[f"%{q.lower()}%",f"%{q.lower()}%"]
    sql+=" GROUP BY s.id ORDER BY s.name"
    rows=[dict(r) for r in c.execute(sql,args).fetchall()]; c.close()
    return rows
@app.get("/api/shops/{shop_id}")
def shop_detail(shop_id:str):
    c=conn(); s=c.execute("SELECT * FROM shops WHERE id=?",(shop_id,)).fetchone()
    if not s: c.close(); raise HTTPException(404,"Shop not found")
    foods=c.execute("SELECT f.*,s.name shop_name FROM foods f JOIN shops s ON s.id=f.shop_id WHERE f.shop_id=? AND f.active=1 AND f.food_type!='addons' ORDER BY f.food_type, f.name",(shop_id,)).fetchall()
    result=dict(s); result["foods"]=[]
    for raw in foods:
        d=enrich_food(raw)
        d["addons"]=[dict(a) for a in c.execute("SELECT id,name,price_sen,description FROM foods WHERE parent_food_id=? AND food_type='addons' AND active=1 AND available=1",(d["id"],)).fetchall()]
        result["foods"].append(d)
    c.close(); return result
@app.get("/api/foods")
def foods(q:str="",category:str="",tag:str="",shop_id:str="",limit:int=300):
    c=conn(); sql="SELECT f.*,s.name shop_name FROM foods f JOIN shops s ON s.id=f.shop_id WHERE f.active=1 AND f.food_type!='addons'"; args=[]
    if category: sql+=" AND lower(f.category)=lower(?)"; args.append(category)
    if shop_id: sql+=" AND f.shop_id=?"; args.append(shop_id)
    sql+=" ORDER BY f.top_selling DESC, f.todays_special DESC, f.name LIMIT ?"; args.append(min(limit,300))
    rows=[]
    for raw in c.execute(sql,args).fetchall():
        d=enrich_food(raw)
        d["addons"]=[dict(a) for a in c.execute("SELECT id,name,price_sen,description FROM foods WHERE parent_food_id=? AND food_type='addons' AND active=1 AND available=1",(d["id"],)).fetchall()]
        hay=" ".join([d.get("name", ""),d.get("description", ""),d.get("shop_name", ""),d.get("category", "")]).lower()
        known_tags={"chicken","fish","beef","mutton","non-veg","seafood","veg","vegetarian","beverages","western","asian"}
        query=q.strip().lower()
        aliases={"ayam":"chicken","ikan":"fish","daging":"beef","lembu":"beef","kambing":"mutton","udang":"seafood","sotong":"seafood","ketam":"seafood","sayur":"vegetarian","nasi":"asian","mee":"asian","mi":"asian","kari":"asian","minuman":"beverages"}
        canonical=aliases.get(query,query)
        if query and not ((canonical in d["tags"]) if canonical in known_tags else (query in hay or canonical in d["tags"])): continue
        if tag and tag.lower() not in [x.lower() for x in d["tags"]]: continue
        rows.append(d)
    c.close(); return rows
@app.get("/api/foods/{food_id}")
def food_detail(food_id:str):
    c=conn(); f=c.execute("SELECT f.*,s.name shop_name FROM foods f JOIN shops s ON s.id=f.shop_id WHERE f.id=? AND f.active=1 AND f.food_type!='addons'",(food_id,)).fetchone()
    if not f: c.close(); raise HTTPException(404,"Food not found")
    d=enrich_food(f)
    d["addons"]=[dict(a) for a in c.execute("SELECT id,name,price_sen,description FROM foods WHERE parent_food_id=? AND food_type='addons' AND active=1 AND available=1",(food_id,)).fetchall()]
    c.close(); return d

@app.get("/api/home")
def home(user=Depends(current_user)):
    c=conn()
    categories=[r[0] for r in c.execute("SELECT DISTINCT category FROM foods WHERE food_type!='addons' AND active=1 ORDER BY category").fetchall()]
    popular=[enrich_food(r) for r in c.execute("SELECT f.*,s.name shop_name FROM foods f JOIN shops s ON s.id=f.shop_id WHERE f.active=1 AND f.food_type!='addons' ORDER BY f.top_selling DESC,f.name LIMIT 8").fetchall()]
    shops_data=[dict(r) for r in c.execute("SELECT * FROM shops ORDER BY name LIMIT 6").fetchall()]
    c.close(); return {"categories":categories,"popular":popular,"shops":shops_data}

@app.post("/api/orders")
def create_order(body:OrderCreate,user=Depends(current_user)):
    if user["role"]!="student": raise HTTPException(403,"Student account required")
    if not body.items: raise HTTPException(400,"Your cart is empty")
    c=conn(); total=0; prepared=[]
    shop=c.execute("SELECT is_open FROM shops WHERE id=?",(body.shop_id,)).fetchone()
    if not shop or not shop["is_open"]: c.close(); raise HTTPException(400,"This shop is currently closed and cannot accept new orders")
    for item in body.items:
        f=c.execute("SELECT * FROM foods WHERE id=? AND shop_id=? AND active=1 AND available=1 AND food_type!='addons'",(item.food_id,body.shop_id)).fetchone()
        if not f: c.close(); raise HTTPException(400,"A selected food is unavailable")
        unit=money(f["price_sen"]); addons=[]; tray=[]
        for aid in item.addon_ids:
            a=c.execute("SELECT * FROM foods WHERE id=? AND shop_id=? AND food_type='addons' AND active=1 AND available=1",(aid,body.shop_id)).fetchone()
            if not a: c.close(); raise HTTPException(400,"An add-on is unavailable")
            addons.append({"id":a["id"],"name":a["name"],"price_sen":a["price_sen"]}); unit+=money(a["price_sen"])
        for tid in item.tray_food_ids:
            t=c.execute("SELECT * FROM foods WHERE id=? AND shop_id=? AND food_type!='addons' AND active=1 AND available=1",(tid,body.shop_id)).fetchone()
            if not t: c.close(); raise HTTPException(400,"A lunch-set selection is unavailable")
            tray.append({"id":t["id"],"name":t["name"],"price_sen":t["price_sen"]}); unit+=money(t["price_sen"])
        total+=unit*item.quantity; prepared.append((f,item,unit,addons,tray))
    if body.payment_method not in ("APay","APCard"): c.close(); raise HTTPException(400,"Choose a valid payment method")
    reference="MS"+datetime.now().strftime("%y%m%d")+secrets.token_hex(2).upper(); token=secrets.token_urlsafe(32)
    cur=c.execute("INSERT INTO orders(reference,student_id,shop_id,total_sen,qr_token,payment_method) VALUES(?,?,?,?,?,?)",(reference,user["id"],body.shop_id,total,token,body.payment_method)); oid=cur.lastrowid
    for f,item,unit,addons,tray in prepared:
        details={"addons":addons,"lunch_set":tray}
        order_name=f["name"]
        if tray:
            shop_name=c.execute("SELECT name FROM shops WHERE id=?",(body.shop_id,)).fetchone()[0]
            order_name=f"{shop_name} Lunch Set"
        c.execute("INSERT INTO order_items(order_id,food_id,food_name,quantity,unit_price_sen,addons_json,notes) VALUES(?,?,?,?,?,?,?)",(oid,f["id"],order_name,item.quantity,unit,json.dumps(details),item.notes))
    c.commit(); c.close(); return {"id":oid,"reference":reference,"status":"pending","total_sen":total,"qr_token":token}
@app.get("/api/orders")
def list_orders(scope:str="all",user=Depends(current_user)):
    c=conn()
    where=[]; args=[]
    if user["role"]=="student":
        where.append("o.student_id=?"); args.append(user["id"])
    else:
        where.append("o.shop_id=?"); args.append(user["shop_id"])
    if scope=="active":
        where.append("o.status NOT IN ('collected','cancelled')")
    sql="SELECT o.*,s.name shop_name FROM orders o JOIN shops s ON s.id=o.shop_id WHERE "+" AND ".join(where)
    if user["role"]=="vendor" and scope=="active":
        sql+=" ORDER BY CASE o.status WHEN 'pending' THEN 0 WHEN 'preparing' THEN 1 WHEN 'ready' THEN 2 ELSE 3 END, o.created_at DESC LIMIT 150"
    elif user["role"]=="vendor":
        sql+=" ORDER BY CASE WHEN o.status NOT IN ('collected','cancelled') THEN 0 ELSE 1 END, o.created_at DESC LIMIT 500"
    else:
        sql+=" ORDER BY o.created_at DESC LIMIT 100"
    rows=[dict(r) for r in c.execute(sql,args).fetchall()]
    if rows:
        ids=[r["id"] for r in rows]
        marks=",".join("?" for _ in ids)
        items={}
        for x in c.execute(f"SELECT * FROM order_items WHERE order_id IN ({marks}) ORDER BY id",ids).fetchall():
            items.setdefault(x["order_id"],[]).append(dict(x))
        reviewed={r[0] for r in c.execute(f"SELECT order_id FROM reviews WHERE order_id IN ({marks})",ids).fetchall()}
        for r in rows:
            r["items"]=items.get(r["id"],[])
            r["has_review"]=r["id"] in reviewed
    c.close()
    return rows
@app.get("/api/orders/{order_id}")
def order_detail(order_id:int,user=Depends(current_user)):
    c=conn(); r=c.execute("SELECT o.*,s.name shop_name FROM orders o JOIN shops s ON s.id=o.shop_id WHERE o.id=?",(order_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,"Order not found")
    if user["role"]=="student" and r["student_id"]!=user["id"]: c.close(); raise HTTPException(403,"Not your order")
    if user["role"]=="vendor" and r["shop_id"]!=user["shop_id"]: c.close(); raise HTTPException(403,"Not your shop")
    d=dict(r); d["items"]=[dict(x) for x in c.execute("SELECT * FROM order_items WHERE order_id=?",(order_id,)).fetchall()]; c.close(); return d
@app.get("/api/orders/{order_id}/qr")
def order_qr(order_id:int,user=Depends(current_user)):
    c=conn(); r=c.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Order not found")
    if user["role"]=="student" and r["student_id"]!=user["id"]: raise HTTPException(403,"Not your order")
    if r["status"]!="ready": raise HTTPException(409,"Pickup QR becomes available when your order is ready")
    img=qrcode.make(r["qr_token"]); buf=io.BytesIO(); img.save(buf,format="PNG"); return Response(buf.getvalue(),media_type="image/png")
@app.post("/api/vendor/scan")
def scan_qr(payload:dict,user=Depends(current_user)):
    sid=vendor_shop(user); token=payload.get("token","")
    c=conn(); r=c.execute("SELECT * FROM orders WHERE qr_token=? AND shop_id=?",(token,sid)).fetchone()
    if not r: c.close(); raise HTTPException(404,"Invalid QR code for this shop")
    if r["status"]!="ready": c.close(); raise HTTPException(400,"Order must be marked ready before collection")
    c.execute("UPDATE orders SET status='collected',collected_at=CURRENT_TIMESTAMP WHERE id=?",(r["id"],)); c.commit(); out=dict(r); c.close()
    return {"message":"Order collected successfully","reference":out["reference"]}
@app.patch("/api/vendor/orders/{order_id}")
def update_order(order_id:int,body:StatusUpdate,user=Depends(current_user)):
    sid=vendor_shop(user)
    if body.status not in ["preparing","ready","cancelled"]: raise HTTPException(400,"Invalid status")
    c=conn(); r=c.execute("SELECT * FROM orders WHERE id=? AND shop_id=?",(order_id,sid)).fetchone()
    if not r: c.close(); raise HTTPException(404,"Order not found")
    if r["status"] in ["collected","cancelled"]: c.close(); raise HTTPException(400,"This order is closed")
    if body.status=="cancelled" and not body.reject_reason.strip(): c.close(); raise HTTPException(400,"Please provide a reason for rejecting this order")
    c.execute("UPDATE orders SET status=?,reject_reason=?,prep_minutes=? WHERE id=?",(body.status,body.reject_reason.strip(),body.prep_minutes,order_id)); c.commit(); c.close(); return {"ok":True}

@app.get("/api/vendor/foods")
def vendor_foods(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); rows=[dict(r) for r in c.execute("SELECT f.*,COALESCE(fc.cost_sen,0) cost_sen FROM foods f LEFT JOIN food_costs fc ON fc.food_id=f.id WHERE f.shop_id=? ORDER BY f.food_type,f.name",(sid,)).fetchall()]; c.close(); return rows
@app.patch("/api/vendor/foods/{food_id}")
def update_food(food_id:str,body:FoodUpdate,user=Depends(current_user)):
    sid=vendor_shop(user); fields=body.model_dump(exclude_unset=True)
    if sid not in ("SHOP001","SHOP005") and fields.get("lunch_set_enabled"):
        raise HTTPException(400,"Lunch Sets are available only for Chinese and Mamak")
    if sid not in ("SHOP001","SHOP005") and "lunch_set_enabled" in fields:
        fields["lunch_set_enabled"]=False
    if not fields: raise HTTPException(400,"No changes supplied")
    allowed={"name","description","category","price_sen","available","active","todays_special","image_url","nutrition_json","lunch_set_enabled"}
    if set(fields)-allowed: raise HTTPException(400,"Unsupported fields")
    c=conn(); cur=c.execute("UPDATE foods SET "+",".join(f"{k}=?" for k in fields)+" WHERE id=? AND shop_id=?",(*[int(v) if isinstance(v,bool) else v for v in fields.values()],food_id,sid))
    c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Food not found")
    return {"ok":True}
@app.get("/api/vendor/summary")
def vendor_summary(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn()
    orders=c.execute("SELECT COUNT(*) n,COALESCE(SUM(total_sen),0) sales FROM orders WHERE shop_id=? AND status!='cancelled'",(sid,)).fetchone()
    pending=c.execute("SELECT COUNT(*) FROM orders WHERE shop_id=? AND status IN ('pending','preparing')",(sid,)).fetchone()[0]
    best=[dict(r) for r in c.execute("SELECT oi.food_name,SUM(oi.quantity) quantity,SUM(oi.quantity*oi.unit_price_sen) revenue FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE o.shop_id=? AND o.status!='cancelled' GROUP BY oi.food_id ORDER BY quantity DESC LIMIT 5",(sid,)).fetchall()]
    feedback=c.execute("SELECT COUNT(*) n,ROUND(AVG(rating),1) avg FROM reviews WHERE shop_id=?",(sid,)).fetchone()
    preparing=c.execute("SELECT COUNT(*) FROM orders WHERE shop_id=? AND status='preparing'",(sid,)).fetchone()[0]
    waiting=c.execute("SELECT COUNT(*) FROM orders WHERE shop_id=? AND status='pending'",(sid,)).fetchone()[0]
    c.close(); return {"orders":orders["n"],"sales_sen":orders["sales"],"pending":pending,"waiting":waiting,"preparing":preparing,"feedback_count":feedback["n"],"average_rating":feedback["avg"] or 0,"best_sellers":best}

@app.post("/api/vendor/foods/image-upload")
async def upload_food_image(file: UploadFile = File(...), user=Depends(current_user)):
    vendor_shop(user)
    allowed={"image/jpeg":".jpg","image/png":".png","image/webp":".webp"}
    if file.content_type not in allowed:
        raise HTTPException(400,"Upload a JPG, PNG or WebP image.")
    data=await file.read(6*1024*1024+1)
    if len(data)>6*1024*1024:
        raise HTTPException(413,"Image must be 6 MB or smaller.")
    name=f"food_{uuid.uuid4().hex}{allowed[file.content_type]}"
    (MEDIA_DIR/name).write_bytes(data)
    return {"image_url":f"/media/{name}"}

@app.post("/api/vendor/foods/generate-image")
def generate_food_image(payload:dict, user=Depends(current_user)):
    vendor_shop(user)
    api_key=os.getenv("OPENAI_API_KEY","").strip()
    if not api_key:
        raise HTTPException(503,"AI image generation is not configured. Add OPENAI_API_KEY to backend/.env and restart the server.")
    food_name=str(payload.get("name","")).strip()[:120]
    description=str(payload.get("description","")).strip()[:500]
    prompt=str(payload.get("prompt","")).strip()[:700]
    if not food_name and not prompt:
        raise HTTPException(400,"Enter a food name or image description.")
    image_prompt=("Create a realistic, appetising professional menu photograph of "+(food_name or "a prepared dish")+
      (". Dish details: "+description if description else "")+
      (". Additional direction: "+prompt if prompt else "")+
      ". Show only the finished food, naturally styled on a clean plate or serving bowl, soft natural restaurant lighting, realistic ingredients and textures, tasteful neutral background, top three-quarter food photography, no text, no logos, no watermark.")
    body=json.dumps({"model":"gpt-image-1","prompt":image_prompt,"size":"1024x1024","quality":"medium"}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/images/generations",data=body,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=90) as response:
            result=json.loads(response.read().decode())
        encoded=result["data"][0].get("b64_json")
        if not encoded: raise ValueError("Image provider returned no image data.")
        data=base64.b64decode(encoded)
        name=f"food_ai_{uuid.uuid4().hex}.png"
        (MEDIA_DIR/name).write_bytes(data)
        return {"image_url":f"/media/{name}"}
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode(errors="ignore")[:500]
        raise HTTPException(502,f"Image generation failed: {detail}")
    except Exception as exc:
        raise HTTPException(502,f"Could not generate the image: {str(exc)[:240]}")

@app.post("/api/vendor/foods")
def create_vendor_food(body:FoodCreate,user=Depends(current_user)):
    sid=vendor_shop(user)
    if body.lunch_set_enabled and sid not in ("SHOP001","SHOP005"):
        raise HTTPException(400,"Lunch Sets are available only for Chinese and Mamak")
    fid=body.id or ("V"+uuid.uuid4().hex[:10].upper())
    c=conn()
    try:
        c.execute("INSERT INTO foods(id,shop_id,name,category,description,price_sen,food_type,available,active,image_url,nutrition_json,tags_json,lunch_set_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (fid,sid,body.name.strip(),body.category,body.description,body.price_sen,body.food_type,int(body.available),1,body.image_url,json.dumps(body.nutrition),json.dumps(body.tags),int(body.lunch_set_enabled)))
        c.execute("INSERT OR REPLACE INTO food_costs(food_id,cost_sen) VALUES(?,?)",(fid,body.cost_sen)); c.commit()
    except sqlite3.IntegrityError:
        c.close(); raise HTTPException(409,"A menu item with this ID already exists")
    c.close(); return {"ok":True,"id":fid}

@app.delete("/api/vendor/foods/{food_id}")
def delete_vendor_food(food_id:str,user=Depends(current_user)):
    sid=vendor_shop(user); c=conn()
    row=c.execute("SELECT id FROM foods WHERE id=? AND shop_id=?",(food_id,sid)).fetchone()
    if not row: c.close(); raise HTTPException(404,"Menu item not found")
    used=c.execute("SELECT COUNT(*) FROM order_items WHERE food_id=?",(food_id,)).fetchone()[0]
    if used:
        c.execute("UPDATE foods SET active=0,available=0 WHERE id=?",(food_id,))
        c.close(); return {"ok":True,"archived":True,"message":"Item has order history, so it was archived instead of permanently deleted."}
    c.execute("DELETE FROM food_costs WHERE food_id=?",(food_id,)); c.execute("DELETE FROM foods WHERE id=?",(food_id,)); c.commit(); c.close(); return {"ok":True,"archived":False}

@app.put("/api/vendor/foods/{food_id}/cost")
def set_food_cost(food_id:str,body:CostUpdate,user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); row=c.execute("SELECT id FROM foods WHERE id=? AND shop_id=?",(food_id,sid)).fetchone()
    if not row: c.close(); raise HTTPException(404,"Menu item not found")
    c.execute("INSERT INTO food_costs(food_id,cost_sen) VALUES(?,?) ON CONFLICT(food_id) DO UPDATE SET cost_sen=excluded.cost_sen",(food_id,body.cost_sen)); c.commit(); c.close(); return {"ok":True}

@app.get("/api/vendor/shop")
def vendor_shop_detail(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); r=c.execute("SELECT * FROM shops WHERE id=?",(sid,)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Shop not found")
    d=dict(r)
    try: d["weekly_hours"]=json.loads(d.get("weekly_hours_json") or "{}")
    except: d["weekly_hours"]={}
    return d

@app.patch("/api/vendor/shop")
def update_vendor_shop(body:ShopUpdate,user=Depends(current_user)):
    sid=vendor_shop(user); fields=body.model_dump(exclude_unset=True)
    if not fields: raise HTTPException(400,"No changes supplied")
    allowed={"is_open","opening_time","closing_time","description","temporary_closed_until"}
    if set(fields)-allowed: raise HTTPException(400,"Unsupported shop fields")
    if "is_open" in fields: fields["is_open"]=int(fields["is_open"])
    c=conn(); cur=c.execute("UPDATE shops SET "+",".join(f"{k}=?" for k in fields)+" WHERE id=?",(*fields.values(),sid)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Shop not found")
    return {"ok":True}

@app.get("/api/vendor/finance")
def vendor_finance(days:int=30,user=Depends(current_user)):
    sid=vendor_shop(user); days=max(1,min(days,365)); start=(date.today()-timedelta(days=days-1)).isoformat(); c=conn()
    summary=c.execute("SELECT COUNT(*) orders,COALESCE(SUM(total_sen),0) revenue FROM orders WHERE shop_id=? AND status NOT IN ('cancelled') AND date(created_at)>=?",(sid,start)).fetchone()
    costs=c.execute("SELECT COALESCE(SUM(oi.quantity*COALESCE(fc.cost_sen,0)),0) cost FROM order_items oi JOIN orders o ON o.id=oi.order_id LEFT JOIN food_costs fc ON fc.food_id=oi.food_id WHERE o.shop_id=? AND o.status NOT IN ('cancelled') AND date(o.created_at)>=?",(sid,start)).fetchone()["cost"]
    best=[dict(r) for r in c.execute("SELECT oi.food_name,SUM(oi.quantity) quantity,SUM(oi.quantity*oi.unit_price_sen) revenue FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE o.shop_id=? AND o.status!='cancelled' AND date(o.created_at)>=? GROUP BY oi.food_id ORDER BY quantity DESC LIMIT 8",(sid,start)).fetchall()]
    daily=[dict(r) for r in c.execute("SELECT date(created_at) day,COUNT(*) orders,COALESCE(SUM(total_sen),0) revenue FROM orders WHERE shop_id=? AND status!='cancelled' AND date(created_at)>=? GROUP BY date(created_at) ORDER BY day",(sid,start)).fetchall()]
    customers=c.execute("SELECT COUNT(DISTINCT student_id) FROM orders WHERE shop_id=? AND status!='cancelled' AND date(created_at)>=?",(sid,start)).fetchone()[0]
    payment_mix=[dict(r) for r in c.execute("SELECT COALESCE(payment_method,'Other') method,COUNT(*) orders,COALESCE(SUM(total_sen),0) revenue FROM orders WHERE shop_id=? AND status!='cancelled' AND date(created_at)>=? GROUP BY payment_method ORDER BY orders DESC",(sid,start)).fetchall()]
    hourly=[dict(r) for r in c.execute("SELECT strftime('%H',created_at) hour,COUNT(*) orders,COALESCE(SUM(total_sen),0) revenue FROM orders WHERE shop_id=? AND status!='cancelled' AND date(created_at)>=? GROUP BY strftime('%H',created_at) ORDER BY orders DESC LIMIT 1",(sid,start)).fetchall()]
    c.close(); revenue=summary["revenue"]
    return {"orders":summary["orders"],"customers":customers,"revenue_sen":revenue,"estimated_cost_sen":costs,"estimated_profit_sen":revenue-costs,"average_order_sen":int(revenue/summary["orders"]) if summary["orders"] else 0,"average_daily_orders":round(summary["orders"]/days,1),"best_sellers":best,"daily":daily,"payment_mix":payment_mix,"peak_hour":hourly[0] if hourly else None,"days":days,"demo_data_note":"DEMO DATA: synthetic demand varies by shop and day (approximately 112–178 orders/day) across 90 days. Student identities are synthetic. Missing item costs are estimated at 40% of selling price; vendors should replace these with actual costs before relying on profit figures."}

@app.post("/api/reviews")
def create_review(payload:dict,user=Depends(current_user)):
    if user["role"]!="student": raise HTTPException(403,"Student account required")
    order_id=int(payload.get("order_id",0)); rating=int(payload.get("rating",0)); comment=str(payload.get("comment", ""))[:1000]
    if rating<1 or rating>5: raise HTTPException(400,"Rating must be between 1 and 5")
    c=conn(); order=c.execute("SELECT * FROM orders WHERE id=? AND student_id=? AND status='collected'",(order_id,user["id"])).fetchone()
    if not order: c.close(); raise HTTPException(400,"Only collected orders can be reviewed")
    if c.execute("SELECT 1 FROM reviews WHERE order_id=?",(order_id,)).fetchone(): c.close(); raise HTTPException(409,"This order has already been reviewed")
    c.execute("INSERT INTO reviews(order_id,student_id,shop_id,rating,comment) VALUES(?,?,?,?,?)",(order_id,user["id"],order["shop_id"],rating,comment)); c.commit(); c.close(); return {"ok":True}

@app.get("/api/vendor/reviews")
def vendor_reviews(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); rows=[dict(r) for r in c.execute("SELECT r.*,u.name student_name,o.reference FROM reviews r JOIN users u ON u.id=r.student_id JOIN orders o ON o.id=r.order_id WHERE r.shop_id=? ORDER BY r.created_at DESC",(sid,)).fetchall()]
    avg=c.execute("SELECT AVG(rating) FROM reviews WHERE shop_id=?",(sid,)).fetchone()[0]; c.close(); return {"average_rating":round(avg or 0,1),"count":len(rows),"reviews":rows}

@app.patch("/api/vendor/reviews/{review_id}")
def reply_review(review_id:int,body:ReviewReply,user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); cur=c.execute("UPDATE reviews SET vendor_reply=? WHERE id=? AND shop_id=?",(body.reply.strip(),review_id,sid)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Review not found")
    return {"ok":True}

@app.post("/api/vendor/nutrition-estimate")
def nutrition_estimate(payload:dict,user=Depends(current_user)):
    vendor_shop(user)
    # Transparent offline demo estimator; this is not a generative AI model.
    ingredients=payload.get("ingredients",[]); serving=max(1,float(payload.get("servings",1) or 1))
    totals={"calories":0.0,"protein_g":0.0,"carbs_g":0.0,"fat_g":0.0,"sugar_g":0.0,"sodium_mg":0.0}
    for item in ingredients:
        grams=max(0,float(item.get("grams",0) or 0)); key=str(item.get("type","")).lower(); factor=grams/100
        profiles={"chicken":(165,31,0,3.6,0,74),"rice":(130,2.7,28,0.3,0.1,1),"fish":(140,24,0,4,0,60),"beef":(250,26,0,15,0,72),"egg":(155,13,1.1,11,1.1,124),"vegetable":(45,2,8,0.3,3,30),"tofu":(76,8,2,4.8,0.6,7),"oil":(884,0,0,100,0,0),"sugar":(387,0,100,0,100,0),"milk":(61,3.2,4.8,3.3,5,43)}
        vals=profiles.get(key)
        if vals:
            for k,v in zip(totals,vals): totals[k]+=v*factor
    totals={k:round(v/serving,1) for k,v in totals.items()}
    return {"nutrition":totals,"source":"Offline ingredient-based demo estimate — not AI-verified or lab-tested","needs_vendor_review":True}

@app.get("/api/spending")
def spending(days:int=30,user=Depends(current_user)):
    if user["role"]!="student": raise HTTPException(403,"Student access only")
    c=conn(); start=(date.today()-timedelta(days=days-1)).isoformat()
    platform=c.execute("SELECT COALESCE(SUM(total_sen),0) FROM orders WHERE student_id=? AND status!='cancelled' AND date(created_at)>=?",(user["id"],start)).fetchone()[0]
    orders=c.execute("SELECT COUNT(*) FROM orders WHERE student_id=? AND status!='cancelled' AND date(created_at)>=?",(user["id"],start)).fetchone()[0]
    manual=c.execute("SELECT COALESCE(SUM(amount_sen),0) FROM expenses WHERE user_id=? AND spent_on>=?",(user["id"],start)).fetchone()[0]
    expenses=[dict(r) for r in c.execute("SELECT * FROM expenses WHERE user_id=? AND spent_on>=? ORDER BY spent_on DESC LIMIT 20",(user["id"],start)).fetchall()]
    budget=c.execute("SELECT monthly_budget_sen FROM budgets WHERE user_id=?",(user["id"],)).fetchone()
    shops_data=[dict(r) for r in c.execute("SELECT s.name shop, SUM(o.total_sen) amount, COUNT(o.id) orders FROM orders o JOIN shops s ON s.id=o.shop_id WHERE o.student_id=? AND o.status!='cancelled' AND date(o.created_at)>=? GROUP BY s.id ORDER BY amount DESC",(user["id"],start)).fetchall()]
    c.close(); total=platform+manual
    return {"total_sen":total,"platform_sen":platform,"manual_sen":manual,"orders":orders,"average_order_sen":int(platform/orders) if orders else 0,"monthly_budget_sen":budget[0] if budget else 0,"expenses":expenses,"by_shop":shops_data}
@app.post("/api/spending/expenses")
def add_expense(body:ExpenseCreate,user=Depends(current_user)):
    if user["role"]!="student": raise HTTPException(403,"Student access only")
    c=conn(); cur=c.execute("INSERT INTO expenses(user_id,description,category,amount_sen,spent_on) VALUES(?,?,?,?,?)",(user["id"],body.description,body.category,body.amount_sen,body.spent_on)); c.commit(); c.close(); return {"id":cur.lastrowid}
@app.delete("/api/spending/expenses/{expense_id}")
def delete_expense(expense_id:int,user=Depends(current_user)):
    c=conn(); cur=c.execute("DELETE FROM expenses WHERE id=? AND user_id=?",(expense_id,user["id"])); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Expense not found")
    return {"ok":True}
@app.put("/api/spending/budget")
def set_budget(body:BudgetUpdate,user=Depends(current_user)):
    c=conn(); c.execute("INSERT INTO budgets(user_id,monthly_budget_sen) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET monthly_budget_sen=excluded.monthly_budget_sen",(user["id"],body.monthly_budget_sen)); c.commit(); c.close(); return {"ok":True}

@app.get("/api/vendor/reports.csv")
def vendor_csv(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); rows=c.execute("SELECT o.reference,o.created_at,o.status,s.name shop,oi.food_name,oi.quantity,oi.unit_price_sen, o.total_sen FROM orders o JOIN shops s ON s.id=o.shop_id JOIN order_items oi ON oi.order_id=o.id WHERE o.shop_id=? ORDER BY o.created_at DESC",(sid,)).fetchall(); c.close()
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Reference","Created","Status","Shop","Food","Quantity","Unit price (RM)","Order total (RM)"])
    for r in rows: w.writerow([r["reference"],r["created_at"],r["status"],r["shop"],r["food_name"],r["quantity"],f'{r["unit_price_sen"]/100:.2f}',f'{r["total_sen"]/100:.2f}'])
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=mealsync-sales-report.csv"})
@app.get("/api/vendor/reports.pdf")
def vendor_report_pdf(user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); shop=c.execute("SELECT name FROM shops WHERE id=?",(sid,)).fetchone()
    rows=c.execute("SELECT reference,created_at,status,total_sen FROM orders WHERE shop_id=? ORDER BY created_at DESC LIMIT 250",(sid,)).fetchall()
    totals=c.execute("SELECT COUNT(*) n,COALESCE(SUM(total_sen),0) revenue FROM orders WHERE shop_id=? AND status!='cancelled'",(sid,)).fetchone(); c.close()
    buf=io.BytesIO(); pdf=canvas.Canvas(buf,pagesize=A4); w,h=A4; y=h-48
    pdf.setFont("Helvetica-Bold",18); pdf.drawString(42,y,"MealSync · Finance Report"); y-=25
    pdf.setFont("Helvetica",10); pdf.drawString(42,y,f"Shop: {shop['name']}  |  Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"); y-=24
    pdf.drawString(42,y,f"Orders: {totals['n']}    Gross revenue: RM {totals['revenue']/100:.2f}"); y-=30
    pdf.setFont("Helvetica-Bold",9); pdf.drawString(42,y,"Reference"); pdf.drawString(165,y,"Date"); pdf.drawString(310,y,"Status"); pdf.drawRightString(w-42,y,"Total (RM)"); y-=16
    pdf.setFont("Helvetica",8)
    for r in rows:
        if y<55: pdf.showPage(); y=h-48; pdf.setFont("Helvetica",8)
        pdf.drawString(42,y,str(r['reference'])); pdf.drawString(165,y,str(r['created_at'])[:16]); pdf.drawString(310,y,str(r['status'])); pdf.drawRightString(w-42,y,f"{r['total_sen']/100:.2f}"); y-=14
    pdf.save(); buf.seek(0); return StreamingResponse(buf,media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=mealsync-finance-report.pdf"})

@app.get("/api/vendor/invoice/{order_id}.pdf")
def invoice(order_id:int,user=Depends(current_user)):
    sid=vendor_shop(user); c=conn(); o=c.execute("SELECT o.*,s.name shop FROM orders o JOIN shops s ON s.id=o.shop_id WHERE o.id=? AND o.shop_id=?",(order_id,sid)).fetchone()
    if not o: c.close(); raise HTTPException(404,"Order not found")
    items=c.execute("SELECT * FROM order_items WHERE order_id=?",(order_id,)).fetchall(); c.close()
    b=io.BytesIO(); p=canvas.Canvas(b,pagesize=A4); p.setFont("Helvetica-Bold",22); p.drawString(48,790,"MealSync")
    p.setFont("Helvetica",11); p.drawString(48,760,"Order invoice"); p.drawString(48,738,f"Reference: {o['reference']}"); p.drawString(48,720,f"Shop: {o['shop']}"); p.drawString(48,702,f"Date: {o['created_at']}")
    y=660; p.setFont("Helvetica-Bold",10); p.drawString(48,y,"Item"); p.drawString(350,y,"Qty"); p.drawString(420,y,"Unit"); p.drawString(500,y,"Total")
    y-=22; p.setFont("Helvetica",10)
    for it in items:
        line_total=it["quantity"]*it["unit_price_sen"]
        p.drawString(48,y,it["food_name"][:42]); p.drawString(355,y,str(it["quantity"])); p.drawString(420,y,f"RM {it['unit_price_sen']/100:.2f}"); p.drawString(500,y,f"RM {line_total/100:.2f}"); y-=20
    p.setFont("Helvetica-Bold",13); p.drawRightString(550,y-20,f"Total: RM {o['total_sen']/100:.2f}"); p.save(); b.seek(0)
    return StreamingResponse(b,media_type="application/pdf",headers={"Content-Disposition":f"attachment; filename={o['reference']}-invoice.pdf"})
