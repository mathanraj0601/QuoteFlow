import json, random, sqlite3, os
from datetime import datetime, timedelta

random.seed(42)
OUT_DIR = "/home/claude/quoteflow_gen"

BROKER_NAME = "Meridian Trade Partners"
BROKER_DOMAIN = "meridiantrade.com"
BROKER_EMAIL = f"procurement@{BROKER_DOMAIN}"

# ---------------------------------------------------------------------------
# 1. SUPPLIERS
# ---------------------------------------------------------------------------
suppliers = [
    dict(id=1, name="Jiangsu Metal Works Co.", contact_email="wang.li@jmw.com", phone="+86 512 6688 1234",
         region="Jiangsu, China", rating=4.5, specialization="Industrial Fasteners",
         certifications="ISO 9001, CE Marking", payment_terms="30% advance, 70% before shipment"),
    dict(id=2, name="Dongguan Fastener Co.", contact_email="sales@dgfastener.cn", phone="+86 769 2233 9981",
         region="Guangdong, China", rating=4.1, specialization="Industrial Fasteners",
         certifications="ISO 9001", payment_terms="Net 30"),
    dict(id=3, name="Thai Steel Supply Ltd.", contact_email="k.somsak@thaisteel.co.th", phone="+66 2 234 5678",
         region="Bangkok, Thailand", rating=3.8, specialization="Industrial Fasteners",
         certifications="TISI, ISO 9001", payment_terms="50% advance, 50% on delivery"),
    dict(id=4, name="Shenzhen Bright Electronics", contact_email="amy.chen@sz-bright.com", phone="+86 755 8899 0011",
         region="Shenzhen, China", rating=4.6, specialization="Electronics Components",
         certifications="RoHS, CE, FCC", payment_terms="T/T 30% deposit, balance before shipment"),
    dict(id=5, name="Taiwan Precision Circuits", contact_email="export@tpc-taiwan.com", phone="+886 2 2777 3344",
         region="Taipei, Taiwan", rating=4.7, specialization="Electronics Components",
         certifications="ISO 9001, UL Listed", payment_terms="Net 45"),
    dict(id=6, name="Vietnam Textile Alliance", contact_email="linh.nguyen@vta.vn", phone="+84 28 3822 1199",
         region="Ho Chi Minh City, Vietnam", rating=4.2, specialization="Textiles",
         certifications="OEKO-TEX, GOTS", payment_terms="30% advance, LC at sight"),
    dict(id=7, name="Dhaka Garments Export Ltd.", contact_email="rahman@dhakagarments.com.bd", phone="+880 2 9887654",
         region="Dhaka, Bangladesh", rating=3.9, specialization="Textiles",
         certifications="WRAP, OEKO-TEX", payment_terms="Net 30"),
    dict(id=8, name="Gujarat Chem Industries", contact_email="patel.r@gujaratchem.in", phone="+91 79 2630 4455",
         region="Ahmedabad, India", rating=4.0, specialization="Chemicals",
         certifications="ISO 9001, REACH Compliant", payment_terms="50% advance, 50% against B/L"),
    dict(id=9, name="Anhui Specialty Chemicals", contact_email="zhao.jun@anhuichem.cn", phone="+86 551 6234 9900",
         region="Anhui, China", rating=4.3, specialization="Chemicals",
         certifications="ISO 14001, REACH", payment_terms="T/T 40% advance"),
    dict(id=10, name="Istanbul Machine Works", contact_email="ahmet.yilmaz@imw.com.tr", phone="+90 212 555 3322",
         region="Istanbul, Turkey", rating=4.4, specialization="Machinery Parts",
         certifications="CE, ISO 9001", payment_terms="30% advance, 70% before dispatch"),
    dict(id=11, name="Ludhiana Precision Tools", contact_email="sharma@lpt-tools.in", phone="+91 161 245 6789",
         region="Ludhiana, India", rating=3.7, specialization="Machinery Parts",
         certifications="ISO 9001", payment_terms="Net 30"),
    dict(id=12, name="Rotterdam Foods B.V.", contact_email="j.devries@rotterdamfoods.nl", phone="+31 10 412 3344",
         region="Rotterdam, Netherlands", rating=4.5, specialization="Food Products",
         certifications="BRC, HACCP, EU Organic", payment_terms="Net 30, LC accepted"),
    dict(id=13, name="Mekong Delta Agri-Export", contact_email="tran.hoa@mekongagri.vn", phone="+84 292 384 5566",
         region="Can Tho, Vietnam", rating=4.1, specialization="Food Products",
         certifications="GlobalG.A.P., HACCP", payment_terms="30% advance, 70% against docs"),
    dict(id=14, name="Guangzhou Pack Solutions", contact_email="li.wei@gzpack.cn", phone="+86 20 3344 5566",
         region="Guangzhou, China", rating=4.0, specialization="Packaging Materials",
         certifications="ISO 9001, FSC Certified", payment_terms="T/T 30% deposit"),
    dict(id=15, name="Polska Medical Supplies", contact_email="kowalski@polmed.pl", phone="+48 22 654 3210",
         region="Warsaw, Poland", rating=4.6, specialization="Medical Supplies",
         certifications="CE Medical, ISO 13485", payment_terms="Net 30"),
    dict(id=16, name="Monterrey Furniture Co.", contact_email="garcia@monterreyfurn.mx", phone="+52 81 8123 4567",
         region="Monterrey, Mexico", rating=3.9, specialization="Furniture",
         certifications="FSC Certified, CARB Phase 2", payment_terms="40% advance, 60% before shipment"),
    dict(id=17, name="Punjab AgriMachinery Ltd.", contact_email="singh.h@punjabagri.in", phone="+91 172 466 7788",
         region="Ludhiana, India", rating=4.2, specialization="Agricultural Equipment",
         certifications="ISO 9001, CE", payment_terms="30% advance, 70% before dispatch"),
    dict(id=18, name="Izmir Steel Fabrication", contact_email="ozturk@izmirsteel.com.tr", phone="+90 232 444 5566",
         region="Izmir, Turkey", rating=4.3, specialization="Machinery Parts",
         certifications="CE, ISO 3834", payment_terms="Net 30"),
]

# ---------------------------------------------------------------------------
# 2. PRODUCTS
# ---------------------------------------------------------------------------
products = [
    dict(id=1, name="Stainless Steel Bolts M10x50", category="Industrial Fasteners",
         description="Grade A2 stainless steel hex bolts, M10 x 50mm, corrosion resistant",
         specifications="Grade A2-70, DIN 933, zinc/stainless finish options", hs_code="7318.15", unit_of_measure="piece"),
    dict(id=2, name="Galvanized Steel Washers M10", category="Industrial Fasteners",
         description="Hot-dip galvanized flat washers, M10", specifications="DIN 125, galvanized coating 50 microns",
         hs_code="7318.22", unit_of_measure="piece"),
    dict(id=3, name="Heavy Duty Marine Fasteners", category="Industrial Fasteners",
         description="Marine-grade 316 stainless fasteners for offshore use",
         specifications="316 stainless, salt-spray tested 1000hrs", hs_code="7318.15", unit_of_measure="piece"),
    dict(id=4, name="Lithium-ion Battery Cells 18650", category="Electronics Components",
         description="3.7V 3000mAh rechargeable lithium-ion cells", specifications="UN38.3 tested, protected/unprotected variants",
         hs_code="8507.60", unit_of_measure="piece"),
    dict(id=5, name="PCB Assembly Boards (Custom)", category="Electronics Components",
         description="4-layer custom PCBs with SMT assembly", specifications="FR4 substrate, ENIG finish, 1oz copper",
         hs_code="8534.00", unit_of_measure="piece"),
    dict(id=6, name="USB-C Connectors (Bulk)", category="Electronics Components",
         description="USB-C Type connectors for consumer electronics assembly", specifications="24-pin, USB 3.1 compliant",
         hs_code="8536.69", unit_of_measure="piece"),
    dict(id=7, name="Cotton Twill Fabric (220gsm)", category="Textiles",
         description="100% cotton twill fabric, dyed, 220gsm", specifications="220 gsm, 150cm width, colorfast dyed",
         hs_code="5209.42", unit_of_measure="meter"),
    dict(id=8, name="Polyester Blend T-Shirts (Blank)", category="Textiles",
         description="65/35 poly-cotton blank t-shirts for private label", specifications="180gsm, S-XXL sizes",
         hs_code="6109.90", unit_of_measure="piece"),
    dict(id=9, name="Denim Fabric (12oz)", category="Textiles",
         description="12oz raw denim fabric, indigo dyed", specifications="12oz, 150cm width, ring-spun yarn",
         hs_code="5209.42", unit_of_measure="meter"),
    dict(id=10, name="Industrial Grade Citric Acid", category="Chemicals",
         description="Food/industrial grade citric acid anhydrous", specifications="99.5% purity, USP/food grade",
         hs_code="2918.14", unit_of_measure="kg"),
    dict(id=11, name="Sodium Lauryl Sulfate (SLS)", category="Chemicals",
         description="Surfactant for detergent and cosmetic manufacturing", specifications="90%+ purity, powder form",
         hs_code="3402.11", unit_of_measure="kg"),
    dict(id=12, name="Epoxy Resin (Industrial)", category="Chemicals",
         description="Two-part industrial epoxy resin system", specifications="Bisphenol-A based, low viscosity",
         hs_code="3907.30", unit_of_measure="kg"),
    dict(id=13, name="CNC Machined Gear Housings", category="Machinery Parts",
         description="Precision CNC machined aluminum gear housings", specifications="6061-T6 aluminum, +/-0.02mm tolerance",
         hs_code="8483.90", unit_of_measure="piece"),
    dict(id=14, name="Industrial Conveyor Rollers", category="Machinery Parts",
         description="Steel conveyor rollers with sealed bearings", specifications="50mm dia, galvanized steel, sealed bearing",
         hs_code="8431.39", unit_of_measure="piece"),
    dict(id=15, name="Hydraulic Cylinder Assemblies", category="Machinery Parts",
         description="Double-acting hydraulic cylinders for industrial equipment", specifications="Bore 80mm, stroke customizable",
         hs_code="8412.21", unit_of_measure="piece"),
    dict(id=16, name="Extra Virgin Olive Oil (Bulk)", category="Food Products",
         description="Cold-pressed extra virgin olive oil, bulk tanker/drums", specifications="Acidity <0.5%, EU organic certified",
         hs_code="1509.10", unit_of_measure="liter"),
    dict(id=17, name="Basmati Rice (Long Grain)", category="Food Products",
         description="Premium long-grain basmati rice, aged", specifications="1121 variety, aged 12 months",
         hs_code="1006.30", unit_of_measure="kg"),
    dict(id=18, name="Frozen Pangasius Fillets", category="Food Products",
         description="IQF frozen pangasius fish fillets, skinless", specifications="IQF, 120-170g portions, HACCP",
         hs_code="0304.62", unit_of_measure="kg"),
    dict(id=19, name="Corrugated Shipping Boxes (Custom)", category="Packaging Materials",
         description="Custom-printed corrugated cardboard shipping boxes", specifications="5-ply, ECT-44, custom print",
         hs_code="4819.10", unit_of_measure="piece"),
    dict(id=20, name="Biodegradable Poly Mailers", category="Packaging Materials",
         description="Compostable poly mailer bags for e-commerce shipping", specifications="Corn-starch based, self-seal",
         hs_code="3923.21", unit_of_measure="piece"),
    dict(id=21, name="Disposable Nitrile Gloves", category="Medical Supplies",
         description="Powder-free nitrile examination gloves", specifications="AQL 1.5, textured fingertips, S/M/L/XL",
         hs_code="4015.12", unit_of_measure="box"),
    dict(id=22, name="Surgical Face Masks (Type IIR)", category="Medical Supplies",
         description="3-ply surgical masks, fluid resistant", specifications="Type IIR, BFE >98%, EN14683",
         hs_code="6307.90", unit_of_measure="box"),
    dict(id=23, name="Solid Wood Dining Chairs", category="Furniture",
         description="Solid oak dining chairs, flat-pack for export", specifications="Solid oak, flat-pack, upholstered seat option",
         hs_code="9401.61", unit_of_measure="piece"),
    dict(id=24, name="Office Ergonomic Chairs", category="Furniture",
         description="Mesh-back ergonomic office chairs with lumbar support", specifications="Mesh back, adjustable armrests, gas lift",
         hs_code="9401.30", unit_of_measure="piece"),
    dict(id=25, name="Compact Tractor Attachments", category="Agricultural Equipment",
         description="Three-point hitch attachments for compact tractors", specifications="Category 1 hitch, powder-coated steel",
         hs_code="8432.80", unit_of_measure="piece"),
    dict(id=26, name="Drip Irrigation Kits", category="Agricultural Equipment",
         description="Complete drip irrigation kits for row-crop farms", specifications="16mm tubing, pressure-compensated emitters",
         hs_code="8424.82", unit_of_measure="kit"),
]

# ---------------------------------------------------------------------------
# 3. CLIENTS
# ---------------------------------------------------------------------------
clients = [
    dict(id=1, name="John Meyer", company="Apex Manufacturing Ltd.", contact_email="john@apex-mfg.com", region="Ohio, USA", order_history_count=3),
    dict(id=2, name="Sarah Whitfield", company="Whitfield Hardware Group", contact_email="s.whitfield@whitfieldhw.co.uk", region="Manchester, UK", order_history_count=1),
    dict(id=3, name="Carlos Mendoza", company="Mendoza Import Export", contact_email="carlos@mendozaie.mx", region="Mexico City, Mexico", order_history_count=5),
    dict(id=4, name="Priya Nair", company="Nair Electronics Trading", contact_email="priya.nair@nairelectronics.in", region="Bangalore, India", order_history_count=2),
    dict(id=5, name="David Kim", company="Pacific Rim Distributors", contact_email="dkim@pacificrimdist.com", region="Los Angeles, USA", order_history_count=8),
    dict(id=6, name="Emma Lawson", company="Lawson Textiles Co.", contact_email="emma@lawsontextiles.com.au", region="Melbourne, Australia", order_history_count=0),
    dict(id=7, name="Ahmed Al-Farsi", company="Al-Farsi Trading LLC", contact_email="ahmed@alfarsitrading.ae", region="Dubai, UAE", order_history_count=4),
    dict(id=8, name="Lisa Bergström", company="Nordic Chem Solutions AB", contact_email="lisa.b@nordicchemsolutions.se", region="Stockholm, Sweden", order_history_count=2),
    dict(id=9, name="Michael O'Brien", company="O'Brien Industrial Supplies", contact_email="mobrien@obriensupplies.ie", region="Dublin, Ireland", order_history_count=6),
    dict(id=10, name="Yuki Tanaka", company="Tanaka Foods Import Co.", contact_email="y.tanaka@tanakafoods.jp", region="Osaka, Japan", order_history_count=1),
    dict(id=11, name="Grace Osei", company="Osei & Sons Trading", contact_email="grace.osei@oseisons.com.gh", region="Accra, Ghana", order_history_count=0),
    dict(id=12, name="Robert Fischer", company="Fischer Medical Distribution GmbH", contact_email="r.fischer@fischermed.de", region="Munich, Germany", order_history_count=3),
    dict(id=13, name="Isabella Rossi", company="Rossi Home & Living", contact_email="isabella@rossihomeliving.it", region="Milan, Italy", order_history_count=2),
    dict(id=14, name="Tom Richardson", company="Richardson AgriSupply", contact_email="tom@richardsonagri.com", region="Iowa, USA", order_history_count=1),
    dict(id=15, name="Natasha Petrova", company="Petrova Packaging Solutions", contact_email="natasha@petrovapack.ru", region="St. Petersburg, Russia", order_history_count=4),
]

with open(f"{OUT_DIR}/raw_entities.json", "w") as f:
    json.dump(dict(suppliers=suppliers, products=products, clients=clients), f, indent=2)

print(f"Suppliers: {len(suppliers)}, Products: {len(products)}, Clients: {len(clients)}")

SUP = {s["id"]: s for s in suppliers}
PROD = {p["id"]: p for p in products}
CLI = {c["id"]: c for c in clients}

# category -> candidate supplier ids
CAT_SUPPLIERS = {
    "Industrial Fasteners": [1, 2, 3],
    "Electronics Components": [4, 5],
    "Textiles": [6, 7],
    "Chemicals": [8, 9],
    "Machinery Parts": [10, 11, 18],
    "Food Products": [12, 13],
    "Packaging Materials": [14],
    "Medical Supplies": [15],
    "Furniture": [16],
    "Agricultural Equipment": [17],
}

# ---------------------------------------------------------------------------
# 4. TRANSACTIONS (26 threads -> 104 emails)
# transaction: client_id, product_id, supplier_id, quantity, currency, incoterm,
#              style (formal/casual/semiformal), typo(bool), status, days_ago
# ---------------------------------------------------------------------------
transactions = [
    dict(id=1,  client_id=1,  product_id=1,  supplier_id=1,  quantity=5000,  currency="USD", incoterm="FOB Shanghai", style="casual",     typo=True,  status="sent",     days_ago=42, urgency="normal"),
    dict(id=2,  client_id=2,  product_id=2,  supplier_id=2,  quantity=20000, currency="GBP", incoterm="FOB Ningbo",   style="formal",     typo=False, status="accepted", days_ago=61, urgency="normal"),
    dict(id=3,  client_id=9,  product_id=3,  supplier_id=1,  quantity=1200,  currency="EUR", incoterm="CIF Dublin",  style="formal",     typo=False, status="sent",     days_ago=15, urgency="high"),
    dict(id=4,  client_id=5,  product_id=4,  supplier_id=4,  quantity=25000, currency="USD", incoterm="FOB Shenzhen",style="semiformal", typo=False, status="accepted", days_ago=90, urgency="normal"),
    dict(id=5,  client_id=4,  product_id=5,  supplier_id=5,  quantity=800,   currency="USD", incoterm="EXW Taipei",  style="formal",     typo=False, status="sent",     days_ago=8,  urgency="high"),
    dict(id=6,  client_id=5,  product_id=6,  supplier_id=4,  quantity=100000,currency="USD", incoterm="FOB Shenzhen",style="casual",     typo=True,  status="rejected", days_ago=70, urgency="normal"),
    dict(id=7,  client_id=6,  product_id=7,  supplier_id=6,  quantity=3000,  currency="AUD", incoterm="FOB Ho Chi Minh", style="semiformal", typo=False, status="sent",   days_ago=25, urgency="normal"),
    dict(id=8,  client_id=6,  product_id=8,  supplier_id=7,  quantity=15000, currency="AUD", incoterm="FOB Dhaka",   style="casual",     typo=False, status="draft",    days_ago=3,  urgency="normal"),
    dict(id=9,  client_id=13, product_id=9,  supplier_id=6,  quantity=2500,  currency="EUR", incoterm="FOB Ho Chi Minh", style="formal",  typo=False, status="accepted", days_ago=110,urgency="low"),
    dict(id=10, client_id=8,  product_id=10, supplier_id=8,  quantity=8000,  currency="USD", incoterm="CIF Stockholm",style="formal",    typo=False, status="sent",     days_ago=20, urgency="normal"),
    dict(id=11, client_id=8,  product_id=11, supplier_id=9,  quantity=4000,  currency="EUR", incoterm="FOB Shanghai",style="semiformal", typo=False, status="sent",     days_ago=5,  urgency="high"),
    dict(id=12, client_id=9,  product_id=12, supplier_id=8,  quantity=6000,  currency="EUR", incoterm="CIF Dublin",  style="casual",     typo=True,  status="rejected", days_ago=55, urgency="normal"),
    dict(id=13, client_id=1,  product_id=13, supplier_id=10, quantity=350,   currency="USD", incoterm="FOB Istanbul",style="formal",     typo=False, status="accepted", days_ago=130,urgency="normal"),
    dict(id=14, client_id=9,  product_id=14, supplier_id=11, quantity=900,   currency="EUR", incoterm="FOB Mumbai",  style="semiformal", typo=False, status="sent",     days_ago=12, urgency="normal"),
    dict(id=15, client_id=3,  product_id=15, supplier_id=18, quantity=60,    currency="USD", incoterm="FOB Izmir",   style="formal",     typo=False, status="sent",     days_ago=18, urgency="high"),
    dict(id=16, client_id=10, product_id=16, supplier_id=12, quantity=20000, currency="JPY", incoterm="CIF Osaka",   style="formal",     typo=False, status="accepted", days_ago=95, urgency="normal"),
    dict(id=17, client_id=7,  product_id=17, supplier_id=13, quantity=50000, currency="USD", incoterm="FOB Ho Chi Minh", style="semiformal", typo=False, status="sent", days_ago=27, urgency="normal"),
    dict(id=18, client_id=7,  product_id=18, supplier_id=13, quantity=12000, currency="USD", incoterm="CIF Dubai",   style="casual",     typo=True,  status="sent",     days_ago=4,  urgency="high"),
    dict(id=19, client_id=15, product_id=19, supplier_id=14, quantity=40000, currency="USD", incoterm="FOB Guangzhou", style="formal",   typo=False, status="accepted", days_ago=140,urgency="low"),
    dict(id=20, client_id=5,  product_id=20, supplier_id=14, quantity=200000,currency="USD", incoterm="FOB Guangzhou", style="casual",   typo=False, status="draft",    days_ago=2,  urgency="normal"),
    dict(id=21, client_id=12, product_id=21, supplier_id=15, quantity=5000,  currency="EUR", incoterm="DAP Munich",  style="formal",     typo=False, status="sent",     days_ago=9,  urgency="high"),
    dict(id=22, client_id=12, product_id=22, supplier_id=15, quantity=10000, currency="EUR", incoterm="DAP Munich",  style="formal",     typo=False, status="accepted", days_ago=75, urgency="normal"),
    dict(id=23, client_id=13, product_id=23, supplier_id=16, quantity=500,   currency="EUR", incoterm="FOB Veracruz",style="semiformal", typo=False, status="sent",     days_ago=31, urgency="normal"),
    dict(id=24, client_id=11, product_id=24, supplier_id=16, quantity=150,   currency="USD", incoterm="FOB Veracruz",style="casual",     typo=True,  status="rejected", days_ago=48, urgency="low"),
    dict(id=25, client_id=14, product_id=25, supplier_id=17, quantity=40,    currency="USD", incoterm="FOB Mumbai",  style="formal",     typo=False, status="sent",     days_ago=6,  urgency="normal"),
    dict(id=26, client_id=14, product_id=26, supplier_id=17, quantity=300,   currency="USD", incoterm="FOB Mumbai",  style="casual",     typo=False, status="sent",     days_ago=1,  urgency="high"),
]

print(f"Transactions: {len(transactions)} -> {len(transactions)*4} emails")

# ---------------------------------------------------------------------------
# 5. PRICING BASELINES per product: (unit_price, moq, lead_time_days, shipping_base)
# ---------------------------------------------------------------------------
PRICE_BASE = {
    1: (0.12, 2000, 18, 450),   2: (0.03, 5000, 14, 220),   3: (0.45, 500, 21, 600),
    4: (1.85, 5000, 25, 2200),  5: (14.50, 200, 20, 350),   6: (0.09, 20000, 15, 800),
    7: (3.20, 1000, 20, 900),   8: (2.10, 3000, 18, 700),   9: (4.60, 1000, 22, 850),
    10: (1.15, 1000, 15, 500),  11: (1.80, 2000, 18, 550),  12: (2.95, 1000, 16, 480),
    13: (28.00, 100, 30, 1200), 14: (9.50, 200, 20, 650),   15: (185.00, 20, 35, 900),
    16: (4.80, 5000, 20, 1800), 17: (0.85, 10000, 25, 1600),18: (3.10, 5000, 18, 1400),
    19: (0.65, 10000, 14, 1100),20: (0.03, 50000, 12, 900), 21: (18.50, 500, 20, 700),
    22: (12.00, 1000, 18, 650), 23: (65.00, 100, 28, 1500), 24: (48.00, 50, 25, 1100),
    25: (420.00, 5, 30, 800),   26: (55.00, 50, 20, 400),
}

def bulk_tiers(unit_price, moq):
    return {
        str(moq): round(unit_price * 1.25, 4),
        str(moq * 3): round(unit_price * 1.08, 4),
        str(moq * 8): round(unit_price, 4),
    }

def money(amount, currency):
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "AUD": "A$", "JPY": "¥"}
    sym = symbols.get(currency, currency + " ")
    if currency == "JPY":
        return f"{sym}{amount:,.0f}"
    return f"{sym}{amount:,.2f}"

def inject_typos(text):
    """Light, believable typos/casualisms for casual-style emails."""
    repl = [
        (" you ", " u "), ("Thanks", "Thanx"), ("please", "pls"),
        ("quantity", "qty"), ("delivery", "delivery"), (" approximately", " approx"),
        ("could you", "could u"), ("  ", " "),
    ]
    out = text
    for a, b in repl[:3]:
        out = out.replace(a, b, 1)
    return out

SIGNOFFS_FORMAL = ["Best regards,", "Kind regards,", "Sincerely,", "Warm regards,"]
SIGNOFFS_CASUAL = ["Thanks,", "Cheers,", "Thanks a lot,", "Best,"]
GREET_FORMAL = ["Dear", "Hello", "Good day"]
GREET_CASUAL = ["Hi", "Hey", "Hi there"]

def rand_choice(seed_key, options):
    r = random.Random(str(seed_key))
    return r.choice(options)

# ---------------------------------------------------------------------------
# 6. EMAIL BODY TEMPLATES
# ---------------------------------------------------------------------------
def email1_client_enquiry(tx, client, product):
    style = tx["style"]
    greet = rand_choice(("g1", tx["id"]), GREET_FORMAL if style == "formal" else GREET_CASUAL)
    sign = rand_choice(("s1", tx["id"]), SIGNOFFS_FORMAL if style != "casual" else SIGNOFFS_CASUAL)
    urgency_line = ""
    if tx["urgency"] == "high":
        urgency_line = " We need this fairly quickly, so a prompt reply would help a lot."
    elif tx["urgency"] == "low":
        urgency_line = " There's no rush on this — just exploring options for now."

    if style == "formal":
        body = (
            f"{greet} Team,\n\n"
            f"My name is {client['name']} and I represent {client['company']}, based in {client['region']}. "
            f"We are currently sourcing {product['name']} and would like to request a quotation.\n\n"
            f"Requirement details:\n"
            f"- Product: {product['name']} ({product['description']})\n"
            f"- Quantity required: {tx['quantity']:,} {product['unit_of_measure']}(s)\n"
            f"- Preferred delivery terms: {tx['incoterm']}\n"
            f"- Specifications: {product['specifications']}\n"
            f"- Delivery location: {client['region']}\n\n"
            f"Could you please advise on unit pricing (including any volume discounts), estimated lead time, "
            f"and required certifications for this product?{urgency_line}\n\n"
            f"Looking forward to your response.\n\n{sign}\n{client['name']}\n{client['company']}\n{client['contact_email']}"
        )
    elif style == "semiformal":
        body = (
            f"{greet},\n\n"
            f"I'm reaching out on behalf of {client['company']} — we're looking to purchase {product['name']} "
            f"and wanted to check if you can help with a quote.\n\n"
            f"We need around {tx['quantity']:,} {product['unit_of_measure']}s, ideally shipped {tx['incoterm']}. "
            f"Specs we're after: {product['specifications']}.{urgency_line}\n\n"
            f"Let me know your best pricing and delivery timeline, plus any certifications that come with the product.\n\n"
            f"{sign}\n{client['name']}\n{client['company']}"
        )
    else:  # casual
        body = (
            f"{greet}!\n\n"
            f"Hope you're doing well. We're looking for about {tx['quantity']:,} {product['unit_of_measure']}s of "
            f"{product['name'].lower()} for {client['company']}, need it delivered to {client['region']} "
            f"({tx['incoterm']} would be great if possible).{urgency_line} Can you send over pricing and how long "
            f"it usually takes to ship?\n\n"
            f"{sign}\n{client['name']}"
        )
        if tx["typo"]:
            body = inject_typos(body)
    subject = f"Enquiry: {product['name']} — {tx['quantity']:,} {product['unit_of_measure']}s needed"
    return subject, body

def email2_quote_request(tx, client, product, supplier):
    body = (
        f"Dear {supplier['name']} Team,\n\n"
        f"We have a client enquiry for the following product and would like to request your best quotation:\n\n"
        f"- Product: {product['name']}\n"
        f"- Specifications: {product['specifications']}\n"
        f"- Quantity: {tx['quantity']:,} {product['unit_of_measure']}(s)\n"
        f"- Delivery terms requested: {tx['incoterm']}\n"
        f"- Destination region: {client['region']}\n\n"
        f"Please provide:\n"
        f"1. Unit pricing with bulk/volume tiers\n"
        f"2. Minimum order quantity (MOQ)\n"
        f"3. Shipping cost and estimated lead time\n"
        f"4. Payment terms\n"
        f"5. Relevant certifications and packing details\n\n"
        f"We would appreciate a response at your earliest convenience.\n\n"
        f"Best regards,\n{BROKER_NAME} — Procurement Desk\n{BROKER_EMAIL}"
    )
    subject = f"Quote Request: {product['name']} — Qty {tx['quantity']:,}"
    return subject, body

def email3_supplier_response(tx, product, supplier, pricing):
    tiers_text = "\n".join([f"   - {qty}+ units: {money(price, tx['currency'])}/unit"
                             for qty, price in pricing["tiers"].items()])
    body = (
        f"Dear {BROKER_NAME} Team,\n\n"
        f"Thank you for your enquiry regarding {product['name']}. Please find our quotation details below:\n\n"
        f"- Unit price (at requested qty): {money(pricing['unit_price'], tx['currency'])}/{product['unit_of_measure']}\n"
        f"- Volume pricing tiers:\n{tiers_text}\n"
        f"- Minimum Order Quantity (MOQ): {pricing['moq']:,} {product['unit_of_measure']}(s)\n"
        f"- Shipping cost ({tx['incoterm']}): {money(pricing['shipping_cost'], tx['currency'])}\n"
        f"- Estimated lead time: {pricing['lead_time_days']} business days after order confirmation\n"
        f"- Payment terms: {supplier['payment_terms']}\n"
        f"- Certifications: {supplier['certifications']}\n"
        f"- Packing: {pricing['packing_details']}\n\n"
        f"Please let us know if you require samples or further documentation to proceed.\n\n"
        f"Best regards,\n{supplier['name']}\n{supplier['contact_email']}\n{supplier['phone']}"
    )
    subject = f"RE: Quote Request — {product['name']}"
    return subject, body

def email4_final_quotation(tx, client, product, supplier, pricing, final_price, valid_until):
    body = (
        f"Dear {client['name']},\n\n"
        f"Thank you for your enquiry. We are pleased to present our quotation for {product['name']}:\n\n"
        f"- Product: {product['name']} ({product['specifications']})\n"
        f"- Quantity: {tx['quantity']:,} {product['unit_of_measure']}(s)\n"
        f"- Unit price: {money(final_price, tx['currency'])}/{product['unit_of_measure']}\n"
        f"- Total product cost: {money(final_price * tx['quantity'], tx['currency'])}\n"
        f"- Shipping ({tx['incoterm']}): {money(pricing['shipping_cost'] * 1.1, tx['currency'])}\n"
        f"- Estimated delivery: {pricing['lead_time_days'] + 5} business days from order confirmation\n"
        f"- Certifications included: {supplier['certifications']}\n"
        f"- Payment terms: 30% advance, balance before shipment\n"
        f"- Quotation valid until: {valid_until}\n\n"
        f"Please let us know if you would like to proceed or if any adjustments are needed.\n\n"
        f"Best regards,\n{BROKER_NAME}\n{BROKER_EMAIL}"
    )
    subject = f"Your Quotation: {product['name']} — {tx['quantity']:,} {product['unit_of_measure']}s"
    return subject, body

# ---------------------------------------------------------------------------
# 7. MAIN GENERATION LOOP
# ---------------------------------------------------------------------------
email_threads = []
quotations = []
packing_slips = []
supplier_products_map = {}  # (supplier_id, product_id) -> record

email_id_counter = 1
now = datetime(2026, 9, 6, 10, 0, 0)

for tx in transactions:
    client = CLI[tx["client_id"]]
    product = PROD[tx["product_id"]]
    supplier = SUP[tx["supplier_id"]]

    base_price, base_moq, base_lead, base_ship = PRICE_BASE[product["id"]]
    r = random.Random(tx["id"] * 7)
    unit_price = round(base_price * r.uniform(0.92, 1.08), 4)
    moq = base_moq
    lead_time_days = base_lead + r.randint(-2, 4)
    shipping_cost = round(base_ship * r.uniform(0.9, 1.15), 2)
    tiers = bulk_tiers(unit_price, moq)
    packing_details = {
        1: "Carton 500pcs, Pallet 5000pcs", 2: "Carton 2000pcs, Pallet 20000pcs", 3: "Carton 200pcs, wooden crate for pallets",
        4: "Anti-static tray, 200pcs/carton", 5: "ESD bag, 50pcs/box", 6: "Reel packaging, 5000pcs/reel",
        7: "Rolls of 50m, poly-wrapped", 8: "Poly bag/piece, carton of 100", 9: "Rolls of 40m, poly-wrapped",
        10: "25kg kraft bags on pallets", 11: "25kg drums", 12: "20kg pails", 13: "Individually boxed, foam-lined crate",
        14: "Carton of 10, palletized", 15: "Wooden crate, custom dims per unit", 16: "20L tins or IBC tote",
        17: "25kg PP bags", 18: "IQF cartons, 10kg/carton, frozen container",
        19: "Bundled 25pcs/bale", 20: "Carton of 1000, poly-wrapped", 21: "Box of 100, 10 boxes/carton",
        22: "Box of 50, 40 boxes/carton", 23: "Flat-pack carton, 1pc/carton", 24: "Flat-pack carton, 1pc/carton",
        25: "Wooden crate, 1 unit/crate", 26: "Carton kit box, 1 kit/carton",
    }[product["id"]]

    key = (supplier["id"], product["id"])
    if key not in supplier_products_map:
        supplier_products_map[key] = dict(
            supplier_id=supplier["id"], product_id=product["id"],
            unit_price=unit_price, moq=moq, bulk_pricing_json=json.dumps(tiers),
            lead_time_days=lead_time_days,
            last_quoted_date=(now - timedelta(days=tx["days_ago"])).strftime("%Y-%m-%d"),
        )
        packing_slips.append(dict(
            id=len(packing_slips) + 1, supplier_id=supplier["id"], product_id=product["id"],
            packaging_details=packing_details,
            weight=f"{round(r.uniform(0.05, 25.0), 2)} kg/unit avg",
            dimensions="Varies by carton — see supplier packing spec",
            documentation_urls=f"https://docs.meridiantrade.com/packing/{supplier['id']}-{product['id']}.pdf",
        ))

    pricing = dict(unit_price=unit_price, tiers=tiers, moq=moq, shipping_cost=shipping_cost,
                    lead_time_days=lead_time_days, packing_details=packing_details)

    margin_pct = round(r.uniform(0.15, 0.25), 3)
    final_price = round(unit_price * (1 + margin_pct), 4)

    created_dt = now - timedelta(days=tx["days_ago"])
    t2 = created_dt + timedelta(hours=3)
    t3 = created_dt + timedelta(hours=r.randint(20, 48))
    t4 = t3 + timedelta(hours=r.randint(4, 20))
    valid_until = (t4 + timedelta(days=14)).strftime("%Y-%m-%d")

    quotation_id = tx["id"]
    quotations.append(dict(
        id=quotation_id, client_id=client["id"], supplier_id=supplier["id"], product_id=product["id"],
        quoted_price=final_price, quantity=tx["quantity"],
        shipping_cost=round(shipping_cost * 1.1, 2), status=tx["status"],
        created_at=created_dt.strftime("%Y-%m-%d %H:%M:%S"),
        sent_at=t4.strftime("%Y-%m-%d %H:%M:%S") if tx["status"] != "draft" else None,
        valid_until=valid_until,
    ))

    # --- Email 1: Client -> Intermediary
    subj1, body1 = email1_client_enquiry(tx, client, product)
    extracted1 = dict(product_name=product["name"], quantity=tx["quantity"], delivery_terms=tx["incoterm"],
                       destination=client["region"], specifications=product["specifications"],
                       client_name=client["name"], client_company=client["company"], urgency=tx["urgency"])
    email_threads.append(dict(
        id=email_id_counter, gmail_message_id=f"msg_{tx['id']:03d}_1", thread_id=tx["id"],
        from_email=client["contact_email"], to_email=BROKER_EMAIL, subject=subj1, body_text=body1,
        direction="inbound", extracted_data_json=json.dumps(extracted1),
        processed_at=created_dt.strftime("%Y-%m-%d %H:%M:%S"), linked_quotation_id=quotation_id,
    )); email_id_counter += 1

    # --- Email 2: Intermediary -> Supplier
    subj2, body2 = email2_quote_request(tx, client, product, supplier)
    email_threads.append(dict(
        id=email_id_counter, gmail_message_id=f"msg_{tx['id']:03d}_2", thread_id=tx["id"],
        from_email=BROKER_EMAIL, to_email=supplier["contact_email"], subject=subj2, body_text=body2,
        direction="outbound", extracted_data_json=None,
        processed_at=t2.strftime("%Y-%m-%d %H:%M:%S"), linked_quotation_id=quotation_id,
    )); email_id_counter += 1

    # --- Email 3: Supplier -> Intermediary
    subj3, body3 = email3_supplier_response(tx, product, supplier, pricing)
    extracted3 = dict(unit_price=unit_price, bulk_pricing=tiers, moq=moq, shipping_cost=shipping_cost,
                       lead_time_days=lead_time_days, payment_terms=supplier["payment_terms"],
                       certifications=supplier["certifications"], packing_details=packing_details)
    email_threads.append(dict(
        id=email_id_counter, gmail_message_id=f"msg_{tx['id']:03d}_3", thread_id=tx["id"],
        from_email=supplier["contact_email"], to_email=BROKER_EMAIL, subject=subj3, body_text=body3,
        direction="inbound", extracted_data_json=json.dumps(extracted3),
        processed_at=t3.strftime("%Y-%m-%d %H:%M:%S"), linked_quotation_id=quotation_id,
    )); email_id_counter += 1

    # --- Email 4: Intermediary -> Client
    subj4, body4 = email4_final_quotation(tx, client, product, supplier, pricing, final_price, valid_until)
    email_threads.append(dict(
        id=email_id_counter, gmail_message_id=f"msg_{tx['id']:03d}_4", thread_id=tx["id"],
        from_email=BROKER_EMAIL, to_email=client["contact_email"], subject=subj4, body_text=body4,
        direction="outbound", extracted_data_json=None,
        processed_at=t4.strftime("%Y-%m-%d %H:%M:%S"), linked_quotation_id=quotation_id,
    )); email_id_counter += 1

supplier_products = list(supplier_products_map.values())
for i, sp in enumerate(supplier_products, start=1):
    sp["id"] = i

print(f"\nGenerated: {len(email_threads)} emails, {len(quotations)} quotations, "
      f"{len(supplier_products)} supplier_products rows, {len(packing_slips)} packing slips")

# ---------------------------------------------------------------------------
# 8. WRITE JSON EXPORT
# ---------------------------------------------------------------------------
all_data = dict(
    clients=clients, suppliers=suppliers, products=products,
    supplier_products=supplier_products, quotations=quotations,
    email_threads=email_threads, packing_slips=packing_slips,
)
with open(f"{OUT_DIR}/quoteflow_data.json", "w") as f:
    json.dump(all_data, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# 9. WRITE SQLITE DB
# ---------------------------------------------------------------------------
db_path = f"{OUT_DIR}/quoteflow.db"
if os.path.exists(db_path):
    os.remove(db_path)
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.executescript("""
CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY, name TEXT, contact_email TEXT, phone TEXT, region TEXT,
    rating REAL, specialization TEXT, certifications TEXT, payment_terms TEXT
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY, name TEXT, category TEXT, description TEXT,
    specifications TEXT, hs_code TEXT, unit_of_measure TEXT
);
CREATE TABLE clients (
    id INTEGER PRIMARY KEY, name TEXT, company TEXT, contact_email TEXT,
    region TEXT, order_history_count INTEGER
);
CREATE TABLE supplier_products (
    id INTEGER PRIMARY KEY, supplier_id INTEGER, product_id INTEGER,
    unit_price REAL, moq INTEGER, bulk_pricing_json TEXT,
    lead_time_days INTEGER, last_quoted_date TEXT,
    FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE TABLE quotations (
    id INTEGER PRIMARY KEY, client_id INTEGER, supplier_id INTEGER, product_id INTEGER,
    quoted_price REAL, quantity INTEGER, shipping_cost REAL, status TEXT,
    created_at TEXT, sent_at TEXT, valid_until TEXT,
    FOREIGN KEY(client_id) REFERENCES clients(id),
    FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE TABLE email_threads (
    id INTEGER PRIMARY KEY, gmail_message_id TEXT, thread_id INTEGER,
    from_email TEXT, to_email TEXT, subject TEXT, body_text TEXT,
    direction TEXT, extracted_data_json TEXT, processed_at TEXT, linked_quotation_id INTEGER,
    FOREIGN KEY(linked_quotation_id) REFERENCES quotations(id)
);
CREATE TABLE packing_slips (
    id INTEGER PRIMARY KEY, supplier_id INTEGER, product_id INTEGER,
    packaging_details TEXT, weight TEXT, dimensions TEXT, documentation_urls TEXT,
    FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
""")

def insert_many(table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

insert_many("suppliers", suppliers)
insert_many("products", products)
insert_many("clients", clients)
insert_many("supplier_products", supplier_products)
insert_many("quotations", quotations)
insert_many("email_threads", email_threads)
insert_many("packing_slips", packing_slips)

conn.commit()

# sanity check counts
for t in ["suppliers", "products", "clients", "supplier_products", "quotations", "email_threads", "packing_slips"]:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"{t}: {cur.fetchone()[0]} rows")

conn.close()
print("\nDone. Files written:", f"{OUT_DIR}/quoteflow.db", f"{OUT_DIR}/quoteflow_data.json")

