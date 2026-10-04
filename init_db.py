"""
init_db.py
初始化 SQLite CRM 数据库：建表 + 灌测试数据 + 建视图
用法：python init_db.py
"""

import sqlite3
import os

DB_PATH = "crm.db"


def create_tables(conn):
    cur = conn.cursor()

    # 1. 客户主档
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        customer_id       TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        age               INTEGER,
        gender            TEXT,
        nationality       TEXT,
        city              TEXT,
        rm_id             TEXT,
        rm_name           TEXT,
        branch            TEXT,
        segment           TEXT,
        risk_profile      TEXT,
        kyc_expiry_date   TEXT,
        onboard_date      TEXT,
        contact_phone     TEXT,
        contact_email     TEXT,
        is_active         INTEGER DEFAULT 1
    )""")

    # 2. 持仓主表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS holdings (
        holding_id        TEXT PRIMARY KEY,
        customer_id       TEXT NOT NULL,
        asset_class       TEXT,
        product_type      TEXT,
        product_name      TEXT,
        currency          TEXT,
        amount            REAL,
        amount_usd        REAL,
        start_date        TEXT,
        maturity_date     TEXT,
        status            TEXT,
        risk_level        TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    )""")

    # 3~9. 产品明细表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bond_details (
        holding_id        TEXT PRIMARY KEY,
        issuer            TEXT,
        coupon_rate       REAL,
        coupon_freq       TEXT,
        credit_rating     TEXT,
        next_coupon_date  TEXT,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_details (
        holding_id        TEXT PRIMARY KEY,
        ticker            TEXT,
        market            TEXT,
        shares            REAL,
        cost_price        REAL,
        current_price     REAL,
        sector            TEXT,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fund_details (
        holding_id        TEXT PRIMARY KEY,
        fund_code         TEXT,
        fund_house        TEXT,
        fund_category     TEXT,
        nav               REAL,
        units             REAL,
        region            TEXT,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fx_details (
        holding_id        TEXT PRIMARY KEY,
        pair              TEXT,
        position_type     TEXT,
        forward_rate      REAL,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_details (
        holding_id        TEXT PRIMARY KEY,
        interest_rate     REAL,
        term_months       INTEGER,
        auto_renew        INTEGER,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS structured_details (
        holding_id        TEXT PRIMARY KEY,
        underlying        TEXT,
        barrier_level     REAL,
        strike_price      REAL,
        coupon_rate       REAL,
        capital_protected INTEGER,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS insurance_details (
        holding_id        TEXT PRIMARY KEY,
        policy_type       TEXT,
        premium           REAL,
        insured_person    TEXT,
        beneficiary       TEXT,
        FOREIGN KEY (holding_id) REFERENCES holdings(holding_id)
    )""")

    # 10. 互动记录
    cur.execute("""
    CREATE TABLE IF NOT EXISTS interactions (
        interaction_id    TEXT PRIMARY KEY,
        customer_id       TEXT,
        rm_id             TEXT,
        interaction_date  TEXT,
        channel           TEXT,
        topic             TEXT
    )""")

    # 11. 审计日志
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        queried_by        TEXT,
        query_text        TEXT,
        parsed_conditions TEXT,
        returned_ids      TEXT,
        result_count      INTEGER,
        action_type       TEXT,
        created_at        TEXT
    )""")

    # 12. AUM 视图
    cur.execute("DROP VIEW IF EXISTS customer_aum")
    cur.execute("""
    CREATE VIEW customer_aum AS
    SELECT
        c.customer_id,
        c.name,
        c.age,
        c.gender,
        c.city,
        c.rm_id,
        c.rm_name,
        c.branch,
        c.segment,
        c.risk_profile,
        c.kyc_expiry_date,
        c.contact_phone,
        c.contact_email,
        COALESCE(SUM(h.amount_usd), 0) AS aum_usd,
        COALESCE(SUM(CASE WHEN h.asset_class='DEPOSIT'    THEN h.amount_usd END), 0) AS aum_deposit,
        COALESCE(SUM(CASE WHEN h.asset_class='FUND'       THEN h.amount_usd END), 0) AS aum_fund,
        COALESCE(SUM(CASE WHEN h.asset_class='BOND'       THEN h.amount_usd END), 0) AS aum_bond,
        COALESCE(SUM(CASE WHEN h.asset_class='STOCK'      THEN h.amount_usd END), 0) AS aum_stock,
        COALESCE(SUM(CASE WHEN h.asset_class='FX'         THEN h.amount_usd END), 0) AS aum_fx,
        COALESCE(SUM(CASE WHEN h.asset_class='STRUCTURED' THEN h.amount_usd END), 0) AS aum_structured,
        COALESCE(SUM(CASE WHEN h.asset_class='INSURANCE'  THEN h.amount_usd END), 0) AS aum_insurance
    FROM customers c
    LEFT JOIN holdings h
        ON c.customer_id = h.customer_id AND h.status = 'active'
    GROUP BY c.customer_id
    """)

    conn.commit()
    print("[OK] Tables & view created.")


def seed_customers(conn):
    cur = conn.cursor()
    customers = [
        ('C001','王大明',58,'M','TW','台北','RM01','林专员','台北分行','UHNW','C3','2027-03-15','2015-06-01','0912-111-001','wm001@pb.com',1),
        ('C002','李美玲',62,'F','TW','台北','RM01','林专员','台北分行','HNW','C2','2026-12-01','2017-09-10','0912-111-002','wm002@pb.com',1),
        ('C003','陳志豪',45,'M','TW','台中','RM02','張专员','台中分行','HNW','C4','2027-06-20','2018-01-15','0912-111-003','wm003@pb.com',1),
        ('C004','張淑芬',55,'F','TW','高雄','RM02','張专员','高雄分行','Affluent','C3','2026-11-30','2019-03-22','0912-111-004','wm004@pb.com',1),
        ('C005','林建宏',60,'M','TW','台北','RM01','林专员','台北分行','UHNW','C2','2027-01-10','2014-05-08','0912-111-005','wm005@pb.com',1),
        ('C006','黃雅婷',50,'F','TW','新竹','RM03','吳专员','新竹分行','HNW','C3','2027-08-01','2016-11-11','0912-111-006','wm006@pb.com',1),
        ('C007','劉俊傑',48,'M','TW','台北','RM01','林专员','台北分行','Affluent','C3','2026-10-05','2020-02-20','0912-111-007','wm007@pb.com',1),
        ('C008','蔡佳蓉',53,'F','TW','台南','RM02','張专员','台南分行','HNW','C2','2027-04-18','2018-07-30','0912-111-008','wm008@pb.com',1),
        ('C009','吳國華',65,'M','TW','台北','RM01','林专员','台北分行','UHNW','C1','2026-11-15','2013-01-20','0912-111-009','wm009@pb.com',1),
        ('C010','鄭怡君',47,'F','TW','桃園','RM03','吳专员','桃園分行','Affluent','C4','2027-09-30','2019-06-18','0912-111-010','wm010@pb.com',1),
        ('C011','許文彬',59,'M','TW','台北','RM01','林专员','台北分行','HNW','C3','2027-02-28','2015-10-05','0912-111-011','wm011@pb.com',1),
        ('C012','周欣怡',56,'F','TW','台中','RM02','張专员','台中分行','UHNW','C2','2026-12-20','2014-08-14','0912-111-012','wm012@pb.com',1),
        ('C013','徐立誠',61,'M','TW','台北','RM01','林专员','台北分行','HNW','C3','2027-05-10','2017-04-02','0912-111-013','wm013@pb.com',1),
        ('C014','何雅雯',44,'F','TW','高雄','RM02','張专员','高雄分行','Affluent','C4','2027-07-22','2020-09-09','0912-111-014','wm014@pb.com',1),
        ('C015','馬志遠',63,'M','TW','台北','RM01','林专员','台北分行','UHNW','C2','2026-11-08','2012-12-01','0912-111-015','wm015@pb.com',1),
        ('C016','潘美惠',52,'F','TW','新竹','RM03','吳专员','新竹分行','HNW','C3','2027-03-03','2018-05-25','0912-111-016','wm016@pb.com',1),
        ('C017','賴建銘',57,'M','TW','台北','RM01','林专员','台北分行','HNW','C3','2027-06-11','2016-02-17','0912-111-017','wm017@pb.com',1),
        ('C018','江雅琪',49,'F','TW','台南','RM02','張专员','台南分行','Affluent','C3','2027-10-01','2019-11-03','0912-111-018','wm018@pb.com',1),
        ('C019','宋文豪',66,'M','TW','台北','RM01','林专员','台北分行','UHNW','C1','2026-10-28','2011-07-19','0912-111-019','wm019@pb.com',1),
        ('C020','唐語彤',46,'F','TW','桃園','RM03','吳专员','桃園分行','HNW','C4','2027-08-15','2020-01-08','0912-111-020','wm020@pb.com',1),
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        customers
    )
    conn.commit()
    print(f"[OK] {len(customers)} customers inserted.")


def seed_holdings(conn):
    cur = conn.cursor()
    holdings = [
        # C001
        ('H001','C001','DEPOSIT','定存','美元6個月定存','USD',1200000,1200000,'2026-04-25','2026-10-25','active','R1'),
        ('H002','C001','FUND','基金','全球股票基金','USD',500000,500000,'2024-01-01',None,'active','R3'),
        ('H003','C001','BOND','債券','美國投資級公司債','USD',400000,400000,'2023-01-01','2028-01-01','active','R2'),
        ('H004','C001','STOCK','股票','台積電','TWD',6000000,190000,'2022-05-01',None,'active','R4'),
        # C005
        ('H005','C005','DEPOSIT','定存','美元3個月定存','USD',800000,800000,'2026-07-30','2026-10-30','active','R1'),
        ('H006','C005','INSURANCE','保單','美元儲蓄險','USD',300000,300000,'2023-01-01',None,'active','R2'),
        ('H007','C005','FUND','基金','新興市場債券基金','USD',400000,400000,'2023-06-01',None,'active','R3'),
        ('H008','C005','FX','外匯','USD/JPY 遠期','USD',200000,200000,'2026-08-01','2026-12-01','active','R2'),
        # C009
        ('H009','C009','DEPOSIT','定存','美元1年定存','USD',600000,600000,'2025-10-22','2026-10-22','active','R1'),
        ('H010','C009','BOND','債券','美國國債','USD',800000,800000,'2022-01-01','2027-01-01','active','R1'),
        ('H011','C009','STOCK','股票','中華電信','TWD',3000000,95000,'2021-01-01',None,'active','R3'),
        # C012
        ('H012','C012','DEPOSIT','定存','美元6個月定存','USD',550000,550000,'2026-04-28','2026-10-28','active','R1'),
        ('H013','C012','FUND','基金','科技基金','USD',400000,400000,'2023-06-01',None,'active','R4'),
        ('H014','C012','STRUCTURED','結構性產品','美股掛鉤結構債','USD',600000,600000,'2026-01-01','2027-01-01','active','R3'),
        # C015
        ('H015','C015','DEPOSIT','定存','美元1年定存','USD',2000000,2000000,'2025-10-31','2026-10-31','active','R1'),
        ('H016','C015','INSURANCE','保單','美元儲蓄險','USD',800000,800000,'2022-01-01',None,'active','R2'),
        ('H017','C015','BOND','債券','新興市場主權債','USD',500000,500000,'2023-01-01','2027-06-01','active','R3'),
        ('H018','C015','STOCK','股票','Apple','USD',300000,300000,'2022-01-01',None,'active','R3'),
        # C019
        ('H019','C019','DEPOSIT','定存','美元9個月定存','USD',1000000,1000000,'2026-01-26','2026-10-26','active','R1'),
        ('H020','C019','BOND','債券','企業債 AAA','USD',600000,600000,'2021-01-01','2027-01-01','active','R1'),
        ('H021','C019','FX','外匯','USD/TWD 即期','USD',150000,150000,'2026-09-01',None,'active','R2'),
        # 邊界
        ('H022','C002','DEPOSIT','定存','美元定存','USD',400000,400000,'2026-04-25','2026-10-25','active','R1'),
        ('H023','C002','FUND','基金','平衡型基金','USD',300000,300000,'2023-01-01',None,'active','R3'),
        ('H024','C013','DEPOSIT','定存','美元定存','USD',700000,700000,'2026-05-05','2026-11-05','active','R1'),
        ('H025','C013','STOCK','股票','NVIDIA','USD',250000,250000,'2023-01-01',None,'active','R4'),
        ('H026','C007','DEPOSIT','定存','台幣定存','TWD',3000000,95000,'2026-04-25','2026-10-25','active','R1'),
        ('H027','C011','DEPOSIT','定存','美元定存','USD',300000,300000,'2026-04-25','2026-10-25','active','R1'),
        ('H028','C003','FUND','基金','平衡基金','USD',250000,250000,'2023-01-01',None,'active','R3'),
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        holdings
    )
    conn.commit()
    print(f"[OK] {len(holdings)} holdings inserted.")


def seed_details(conn):
    cur = conn.cursor()

    cur.executemany("INSERT OR REPLACE INTO bond_details VALUES (?,?,?,?,?,?)", [
        ('H003','Apple Inc.',4.5,'半年','AA','2026-11-01'),
        ('H010','US Treasury',3.8,'半年','AAA','2026-12-01'),
        ('H017','Brazil Govt',6.2,'半年','BBB','2026-12-15'),
        ('H020','台塑集團',3.5,'年','AAA','2026-11-20'),
    ])

    cur.executemany("INSERT OR REPLACE INTO stock_details VALUES (?,?,?,?,?,?,?)", [
        ('H004','2330.TW','TW',10000,550,600,'科技'),
        ('H011','2412.TW','TW',5000,110,120,'電信'),
        ('H018','AAPL','US',1500,180,210,'科技'),
        ('H025','NVDA','US',500,800,1200,'科技'),
    ])

    cur.executemany("INSERT OR REPLACE INTO fund_details VALUES (?,?,?,?,?,?,?)", [
        ('H002','GLB-EQ-001','BlackRock','股票型',1.0,500000,'全球'),
        ('H007','EM-BD-005','PIMCO','債券型',1.0,400000,'新興市場'),
        ('H013','TECH-002','Fidelity','股票型',1.0,400000,'美國'),
        ('H023','BAL-003','Schroders','平衡型',1.0,300000,'全球'),
        ('H028','BAL-003','Schroders','平衡型',1.0,250000,'全球'),
    ])

    cur.executemany("INSERT OR REPLACE INTO deposit_details VALUES (?,?,?,?)", [
        ('H001',5.2,6,0),('H005',5.0,3,1),('H009',4.8,12,0),
        ('H012',5.1,6,1),('H015',5.3,12,0),('H019',4.9,9,0),
        ('H022',5.0,6,0),('H024',5.0,6,0),('H026',1.8,6,0),
        ('H027',4.9,6,0),
    ])

    cur.executemany("INSERT OR REPLACE INTO fx_details VALUES (?,?,?,?)", [
        ('H008','USD/JPY','forward',155.5),
        ('H021','USD/TWD','spot',None),
    ])

    cur.executemany("INSERT OR REPLACE INTO structured_details VALUES (?,?,?,?,?,?)", [
        ('H014','S&P 500',4500,4200,7.5,1),
    ])

    cur.executemany("INSERT OR REPLACE INTO insurance_details VALUES (?,?,?,?,?)", [
        ('H006','儲蓄險',300000,'林建宏','林太太'),
        ('H016','儲蓄險',800000,'馬志遠','馬太太'),
    ])

    conn.commit()
    print("[OK] Product detail tables seeded.")


def verify(conn):
    """快速驗證：跑一次『十月底美元大額定存到期』查詢"""
    cur = conn.cursor()
    sql = """
    SELECT c.customer_id, c.name, a.aum_usd, h.amount_usd, h.maturity_date,
           c.rm_id, c.contact_phone
    FROM customers c
    JOIN customer_aum a ON a.customer_id = c.customer_id
    JOIN holdings h ON h.customer_id = c.customer_id
    WHERE h.asset_class = 'DEPOSIT'
      AND h.currency = 'USD'
      AND h.status = 'active'
      AND h.maturity_date BETWEEN '2026-10-21' AND '2026-10-31'
      AND h.amount_usd >= 500000
    ORDER BY h.amount_usd DESC
    """
    rows = cur.execute(sql).fetchall()
    print("\n=== 驗證：十月底美元大額(≥50萬)定存到期 ===")
    print(f"命中 {len(rows)} 位：")
    for r in rows:
        print(f"  {r[0]} {r[1]}  AUM={r[2]:,.0f}  到期={r[3]:,.0f}  {r[4]}  RM={r[5]}  {r[6]}")

    # 驗證多產品篩選：持有科技股且同時持有債券的客戶
    sql2 = """
    SELECT c.customer_id, c.name
    FROM customers c
    WHERE EXISTS (
        SELECT 1 FROM holdings h JOIN stock_details s ON h.holding_id = s.holding_id
        WHERE h.customer_id = c.customer_id AND s.sector = '科技' AND h.status='active'
    )
    AND EXISTS (
        SELECT 1 FROM holdings h2
        WHERE h2.customer_id = c.customer_id AND h2.asset_class = 'BOND' AND h2.status='active'
    )
    """
    rows2 = cur.execute(sql2).fetchall()
    print(f"\n=== 驗證：持有科技股 且 持有債券 ===")
    print(f"命中 {len(rows2)} 位：", [f"{r[0]} {r[1]}" for r in rows2])


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"[INFO] Removed old {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        create_tables(conn)
        seed_customers(conn)
        seed_holdings(conn)
        seed_details(conn)
        verify(conn)
    finally:
        conn.close()
    print(f"\n[DONE] Database ready at {DB_PATH}")


if __name__ == "__main__":
    main()