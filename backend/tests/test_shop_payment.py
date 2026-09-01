import json
from datetime import datetime
from email.header import decode_header, make_header

import config
import db
import ecpay
import mailer
from routes import shop


def test_categories_and_packages(client, monkeypatch):
    def fake_query(sql, _args=None):
        if "DISTINCT category FROM package WHERE" in sql:
            return [{"category": "蛋黃酥系列"}, {"category": "季節限定"}]
        if "DISTINCT c.category" in sql:
            return [{"category": "綜合系列"}]
        if "FROM package WHERE is_active" in sql:
            return [
                {
                    "id": 1,
                    "name": "禮盒",
                    "description": "desc",
                    "spec": "6入",
                    "category": "蛋黃酥系列",
                    "price": 600,
                    "image": "x.jpg",
                    "tag": "new",
                    "sort_order": 0,
                }
            ]
        if "FROM package_categories" in sql:
            return [{"package_id": 1, "category": "綜合系列"}]
        if "FROM package_products_map" in sql:
            return [{"package_id": 1, "name": "蛋黃酥", "unit": "顆", "quantity": 6}]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "query", fake_query)
    categories = client.get("/api/categories").get_json()["categories"]
    assert categories == ["蛋黃酥系列", "綜合系列", "季節限定"]

    packages = client.get("/api/packages?category=綜合系列").get_json()["packages"]
    assert packages[0]["items"][0]["quantity"] == 6.0
    assert packages[0]["categories"] == ["蛋黃酥系列", "綜合系列"]

    assert client.get("/api/packages?category=鳳凰酥系列").get_json() == {"packages": []}


def test_package_lookup_variants(client, monkeypatch):
    assert client.get("/api/package?product_id=bad").status_code == 400

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/package?product_id=99").status_code == 404
    assert client.get("/api/packages/99").status_code == 404

    monkeypatch.setattr(
        db,
        "query_one",
        lambda *_args, **_kwargs: {
            "id": 1,
            "name": "禮盒",
            "description": "",
            "spec": "",
            "category": "蛋黃酥系列",
            "price": 500,
            "image": "",
            "tag": "",
            "sort_order": 0,
        },
    )

    def fake_query(sql, _args=None):
        if "package_products_map" in sql:
            return [{"name": "蛋黃酥", "unit": "顆", "quantity": 2}]
        return [{"package_id": 1, "category": "綜合系列"}]

    monkeypatch.setattr(db, "query", fake_query)
    result = client.get("/api/packages/1")
    assert result.status_code == 200
    assert result.get_json()["items"][0]["quantity"] == 2.0


def _customer(**overrides):
    base = {"name": "A", "phone": "1", "email": "buyer@example.com"}
    base.update(overrides)
    return base


def _store(method="fami", **overrides):
    """模擬綠界電子地圖選回並簽章的門市。"""
    store = {
        "store_id": "001779",
        "store_name": "信義門市",
        "store_address": "台北市信義區信義路五段7號",
        "sub_type": ecpay.LOGISTICS_SUBTYPE[method],
        "method": method,
    }
    store["signature"] = ecpay.sign_store(store)
    store.update(overrides)
    return store


def test_create_order_validations(client, monkeypatch):
    def post(payload):
        return client.post("/api/orders", json=payload)

    assert post({}).status_code == 400

    # Email 必填且需為合法格式
    assert post({
        "customer": {"name": "A", "phone": "1"},
        "shipping_method": "fami",
        "customer_store": "",
        "items": [{"package_id": 1, "quantity": 1}],
    }).status_code == 400
    bad_email = post({
        "customer": _customer(email="not-an-email"),
        "shipping_method": "fami",
        "items": [{"package_id": 1, "quantity": 1}],
    })
    assert bad_email.status_code == 400
    assert "Email" in bad_email.get_json()["error"]

    # 配送 / 付款方式白名單 (pickup 為舊資料,前台不再開放)
    assert post({
        "customer": _customer(),
        "shipping_method": "pickup",
        "items": [{"package_id": 1, "quantity": 1}],
    }).status_code == 400
    assert post({
        "customer": _customer(),
        "shipping_method": "fami",
        "payment_method": "cash",
        "items": [{"package_id": 1, "quantity": 1}],
    }).status_code == 400

    # 宅配缺地址 / 店到店缺門市
    assert post({
        "customer": _customer(),
        "shipping_method": "delivery",
        "items": [{"package_id": 1, "quantity": 1}],
    }).status_code == 400
    store_missing = post({
        "customer": _customer(),
        "shipping_method": "unimart",
        "items": [{"package_id": 1, "quantity": 1}],
    })
    assert store_missing.status_code == 400
    assert "門市" in store_missing.get_json()["error"]

    # 門市必須來自電子地圖:簽章錯誤、或門市與配送方式不符,都要擋下來
    forged = post({
        "customer": _customer(store=_store("fami", store_name="被竄改的門市")),
        "shipping_method": "fami",
        "items": [{"package_id": 1, "quantity": 1}],
    })
    assert forged.status_code == 400
    assert "驗證失敗" in forged.get_json()["error"]

    mismatched = post({
        "customer": _customer(store=_store("fami")),
        "shipping_method": "unimart",
        "items": [{"package_id": 1, "quantity": 1}],
    })
    assert mismatched.status_code == 400
    assert "不符" in mismatched.get_json()["error"]

    base = {
        "customer": _customer(store=_store("fami")),
        "shipping_method": "fami",
        "items": [],
    }
    assert post(base).status_code == 400
    base["items"] = [{"package_id": 1, "quantity": 0}]
    assert post(base).status_code == 400
    base["items"] = [{"package_id": "bad", "quantity": 1}]
    assert post(base).status_code == 400
    base["items"] = [{"package_id": 1, "quantity": 1}]
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert post(base).status_code == 400


def test_shipping_fee_rules():
    assert shop._shipping_fee("delivery", 500) == config.SHIPPING_FEE
    assert shop._shipping_fee("fami", 500) == config.CVS_SHIPPING_FEE
    assert shop._shipping_fee("unimart", 500) == config.CVS_SHIPPING_FEE
    assert shop._shipping_fee("delivery", config.FREE_SHIPPING_THRESHOLD) == 0
    assert shop._shipping_fee("pickup", 100) == 0


class OrderCursor:
    lastrowid = 42

    def __init__(self):
        self.executed = []
        self.many = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, args):
        self.executed.append((sql, args))

    def executemany(self, sql, rows):
        self.many.append((sql, rows))


class OrderConnection:
    def __init__(self):
        self.cur = OrderCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_create_and_fetch_order(client, monkeypatch):
    conn = OrderConnection()
    mails = []
    monkeypatch.setattr(
        db,
        "query_one",
        lambda *_args, **_kwargs: {"id": 3, "name": "禮盒", "price": 500},
    )
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(shop, "_gen_order_no", lambda: "MS-TEST")
    monkeypatch.setattr(mailer, "send_async", lambda *args: mails.append(args))

    response = client.post(
        "/api/orders",
        json={
            "customer": {
                "name": "王小明", "phone": "0912",
                "email": "buyer@example.com", "address": "台北",
            },
            "shipping_method": "delivery",
            "payment_method": "credit",
            "items": [{"package_id": 3, "quantity": 2}],
        },
    )
    body = response.get_json()
    assert response.status_code == 201
    assert body["order_no"] == "MS-TEST"
    assert body["subtotal"] == 1000
    assert body["shipping_fee"] == 120
    assert conn.committed and conn.closed
    assert conn.cur.many

    # 綠界付款表單:金額與檢查碼須正確
    checkout = body["payment"]
    assert checkout["gateway"] == "ecpay"
    assert checkout["action"].endswith("/Cashier/AioCheckOut/V5")
    assert checkout["params"]["MerchantTradeNo"] == "MS-TEST"
    assert checkout["params"]["TotalAmount"] == "1120"
    assert checkout["params"]["ChoosePayment"] == "Credit"
    assert ecpay.verify(checkout["params"])

    # 訂單成立通知信寄到顧客填寫的 Email
    assert mails and mails[0][0] == "buyer@example.com"
    assert "MS-TEST" in mails[0][1]

    order = {
        "order_no": "MS-TEST",
        "customer_name": "王小明",
        "shipping_method": "delivery",
        "payment_method": "credit",
        "payment_status": "unpaid",
        "status": "pending",
        "subtotal": 1000,
        "shipping_fee": 120,
        "total": 1120,
        "created_at": datetime(2026, 8, 22, 10, 30),
    }
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: order.copy())
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: [{"package_name": "禮盒"}])
    fetched = client.get("/api/orders/MS-TEST?phone=0912").get_json()
    assert fetched["created_at"] == "2026-08-22 10:30"
    assert fetched["items"][0]["package_name"] == "禮盒"

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/orders/none?phone=0").status_code == 404


def test_create_cvs_order_stores_map_selection(client, monkeypatch):
    """店到店訂單存下電子地圖選回的門市,通知信也顯示門市。"""
    conn = OrderConnection()
    mails = []
    monkeypatch.setattr(
        db, "query_one", lambda *_args, **_kwargs: {"id": 3, "name": "禮盒", "price": 500})
    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(shop, "_gen_order_no", lambda: "MS-CVS")
    monkeypatch.setattr(mailer, "send_async", lambda *args: mails.append(args))

    response = client.post("/api/orders", json={
        "customer": _customer(name="王小明", store=_store("unimart")),
        "shipping_method": "unimart",
        "payment_method": "credit",
        "items": [{"package_id": 3, "quantity": 1}],
    })
    assert response.status_code == 201
    assert response.get_json()["shipping_fee"] == config.CVS_SHIPPING_FEE

    inserted = conn.cur.executed[0][1]
    assert "001779" in inserted and "信義門市" in inserted
    assert "台北市信義區信義路五段7號" in inserted
    assert "信義門市 (001779)" in mails[0][2]


def test_ecpay_map_params_and_store_signature():
    params = ecpay.map_params("fami")
    assert params["LogisticsType"] == "CVS"
    assert params["LogisticsSubType"] == "FAMIC2C"
    assert params["MerchantID"] == config.ECPAY_LOGISTICS_MERCHANT_ID
    assert params["IsCollection"] == "N"
    assert params["ServerReplyURL"] == config.ECPAY_MAP_REPLY_URL
    assert params["ExtraData"] == "fami"
    assert params["Device"] == "0"
    assert len(params["MerchantTradeNo"]) == 20 and params["MerchantTradeNo"].isalnum()
    assert ecpay.map_params("unimart", device=1)["LogisticsSubType"] == "UNIMARTC2C"
    assert ecpay.map_params("unimart", device=1)["Device"] == "1"

    store = {"store_id": "001779", "store_name": "信義門市",
             "store_address": "台北市", "sub_type": "FAMIC2C"}
    sig = ecpay.sign_store(store)
    assert ecpay.verify_store(store, sig) is True
    assert ecpay.verify_store({**store, "store_id": "000001"}, sig) is False
    assert ecpay.verify_store(store, "") is False


def test_logistics_map_endpoints(client):
    # 開啟電子地圖:自動送出的表單
    page = client.get("/api/logistics/map?method=fami&device=1").get_data(as_text=True)
    assert config.ECPAY_MAP_URL in page
    assert 'name="LogisticsSubType" value="FAMIC2C"' in page
    assert 'name="Device" value="1"' in page
    assert "map-form" in page and "submit()" in page
    assert client.get("/api/logistics/map?method=blackcat").status_code == 400

    # 綠界回傳選定門市:簽章後以 postMessage 帶回購物車頁
    reply = client.post("/api/logistics/map-reply", data={
        "MerchantID": "2000933",
        "MerchantTradeNo": "MAP20260901120000123",
        "LogisticsSubType": "UNIMARTC2C",
        "CVSStoreID": "991182",
        "CVSStoreName": "美麗門市",
        "CVSAddress": "台北市大安區和平東路一段1號",
        "CVSTelephone": "0223456789",
        "CVSOutSide": "0",
        "ExtraData": "unimart",
    })
    html = reply.get_data(as_text=True)
    assert "美麗門市" in html and "991182" in html
    assert f'postMessage(payload, "{config.PUBLIC_ORIGIN}")' in html

    payload = json.loads(html.split("var payload = ", 1)[1].split(";\n", 1)[0])
    assert payload["source"] == "ecpay-map"
    assert payload["store"]["method"] == "unimart"
    assert payload["store"]["store_phone"] == "0223456789"
    assert ecpay.verify_store(payload["store"], payload["store"]["signature"])

    # ExtraData 不在白名單時不回傳配送方式,前台會要求重新選店
    other = client.post("/api/logistics/map-reply", data={
        "CVSStoreID": "1", "CVSStoreName": "X", "ExtraData": "evil"}).get_data(as_text=True)
    assert json.loads(other.split("var payload = ", 1)[1].split(";\n", 1)[0])["store"]["method"] == ""


def test_ecpay_official_check_mac_value_vector():
    """以附件官方 Skill 的 AIO 測試向量驗證固定輸出。"""
    params = {
        "MerchantID": "3002607",
        "MerchantTradeNo": "Test1234567890",
        "MerchantTradeDate": "2025/01/01 12:00:00",
        "PaymentType": "aio",
        "TotalAmount": "100",
        "TradeDesc": "測試",
        "ItemName": "測試商品",
        "ReturnURL": "https://example.com/notify",
        "ChoosePayment": "ALL",
        "EncryptType": "1",
    }
    assert ecpay.check_mac_value(
        params, "pwFHCqoQZGmho4w6", "EkRm7iFT261dpevs"
    ) == "291CBA324D31FB5A4BBBFDF2CFE5D32598524753AFD4959C3BF590C5B2F57FB2"


def test_ecpay_check_mac_value():
    """驗證 CheckMacValue 的穩定性、欄位變動與 callback 比對。"""
    params = {
        "MerchantID": "2000132",
        "MerchantTradeNo": "MS2026010112000012",
        "MerchantTradeDate": "2026/01/01 12:00:00",
        "PaymentType": "aio",
        "TotalAmount": "1120",
        "TradeDesc": "meishifu online order",
        "ItemName": "禮盒 NT$500 x 2",
        "ReturnURL": "https://meishifu.org/api/payment/notify",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
    }
    mac = ecpay.check_mac_value(params, "5294y06JbISpM5x9", "v77hoKGq4kWxNNIS")
    assert len(mac) == 64 and mac == mac.upper()
    # 同樣參數必定得到同樣結果,任一欄位變動就會改變檢查碼
    assert ecpay.check_mac_value(params, "5294y06JbISpM5x9", "v77hoKGq4kWxNNIS") == mac
    changed = {**params, "TotalAmount": "1"}
    assert ecpay.check_mac_value(changed, "5294y06JbISpM5x9", "v77hoKGq4kWxNNIS") != mac
    # CheckMacValue 本身不列入計算
    assert ecpay.verify({**params, "CheckMacValue": mac}) is True
    assert ecpay.verify({**params, "CheckMacValue": "WRONG"}) is False
    assert ecpay.verify(params) is False


def test_ecpay_build_checkout_atm():
    items = [(1, "禮盒#A", 500, 2, 1000)]
    checkout = ecpay.build_checkout("MS-ATM", 1070, "transfer", items, 70)
    params = checkout["params"]
    assert params["ChoosePayment"] == "ATM"
    assert params["ExpireDate"] == "3"
    assert params["PaymentInfoURL"] == config.PAY_INFO_URL
    assert "運費 NT$70 x 1" in params["ItemName"]
    assert params["ItemName"].startswith("禮盒 A NT$500 x 2")  # 商品名內的 # 已被取代
    assert params["ClientBackURL"].endswith("order_no=MS-ATM")
    assert ecpay.verify(params)
    assert ecpay.atm_info({"BankCode": "808", "vAccount": "1234", "ExpireDate": "2026/09/05"})
    assert ecpay.atm_info({}) == ""


def _signed(**fields):
    data = {"MerchantTradeNo": "MS1", "RtnCode": "1", "TradeAmt": "900",
            "TradeNo": "EC123", "PaymentDate": "2026/09/01 12:00:00"}
    data.update(fields)
    data["CheckMacValue"] = ecpay.check_mac_value(data)
    return data


def test_payment_notify(client, monkeypatch):
    calls = []
    order = {"id": 1, "order_no": "MS1", "customer_name": "王小明",
             "email": "buyer@example.com", "total": 900, "payment_status": "unpaid"}
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: order.copy())
    monkeypatch.setattr(db, "execute", lambda sql, args=None: calls.append((sql, args)) or 1)
    monkeypatch.setattr(mailer, "send_async", lambda *args: None)

    ok = client.post("/api/payment/notify", data=_signed())
    assert ok.get_data(as_text=True) == "1|OK"
    assert "payment_status = 'paid'" in calls[0][0]

    # 驗章失敗 / 金額不符 / 綠界回報失敗,都不得標記為已付款
    calls.clear()
    forged = client.post("/api/payment/notify", data={"MerchantTradeNo": "MS1", "RtnCode": "1"})
    assert forged.get_data(as_text=True) == "0|CheckMacValue Error"
    assert client.post("/api/payment/notify", data=_signed(TradeAmt="1")).get_data(as_text=True) == "0|FAIL"
    assert client.post("/api/payment/notify", data=_signed(RtnCode="10100248")).get_data(as_text=True) == "0|FAIL"
    assert not calls

    # 正式後台的模擬付款通知只需回覆已收到，不得更新訂單。
    simulated = client.post("/api/payment/notify", data=_signed(SimulatePaid="1"))
    assert simulated.get_data(as_text=True) == "1|OK"
    assert not calls

    # ATM 取號成功:仍是未付款,但記下虛擬帳號
    atm = client.post("/api/payment/notify", data=_signed(RtnCode="2", BankCode="808", vAccount="9001"))
    assert atm.get_data(as_text=True) == "1|OK"
    assert "payment_info" in calls[0][0]

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.post("/api/payment/notify", data=_signed()).get_data(as_text=True) == "0|FAIL"


def test_payment_result_redirects_to_frontend(client, monkeypatch):
    order = {"id": 1, "order_no": "MS1", "customer_name": "王小明",
             "email": "", "total": 900, "payment_status": "unpaid"}
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: order.copy())
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 1)

    res = client.post("/api/payment/result", data=_signed())
    assert res.status_code == 303
    assert "order_no=MS1" in res.headers["Location"]
    assert "result=paid" in res.headers["Location"]

    forged = client.post("/api/payment/result", data={"MerchantTradeNo": "MS1"})
    assert "result=invalid" in forged.headers["Location"]


def test_payment_status_and_mock_pay(client, monkeypatch):
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert client.get("/api/payment/status/MS404").status_code == 404
    assert client.post("/api/payment/mock-pay", json={"order_no": "missing"}).status_code == 404

    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: {
        "order_no": "MS1", "payment_status": "paid", "status": "paid",
        "payment_method": "transfer", "shipping_method": "unimart",
        "subtotal": 900, "shipping_fee": 70, "total": 970, "payment_info": "",
    })
    body = client.get("/api/payment/status/MS1").get_json()
    assert body["payment_label"] == "銀行 ATM 轉帳"
    assert body["shipping_label"] == "7-11 交貨便"
    assert client.post("/api/payment/mock-pay", json={"order_no": "MS1"}).get_json()["status"] == "paid"

    monkeypatch.setattr(config, "ECPAY_ENV", "production")
    assert client.post("/api/payment/mock-pay", json={"order_no": "MS1"}).status_code == 404


def test_mailer_renders_and_skips_without_smtp(monkeypatch):
    order = {
        "order_no": "MS1", "customer_name": "王小明", "phone": "0912",
        "email": "buyer@example.com", "address": "台北", "store_name": "",
        "shipping_method": "fami", "payment_method": "credit",
        "subtotal": 1000, "shipping_fee": 70, "total": 1070,
    }
    html = mailer.render_order_created(order, [(1, "禮盒", 500, 2, 1000)])
    assert "MS1" in html and "全家店到店" in html and "NT$ 1,070" in html
    assert "MS1" in mailer.render_payment_success(order)

    monkeypatch.setitem(config.MAIL, "host", "")
    mailer._send("buyer@example.com", "主旨", "<p>內容</p>")  # 不會真的連線

    sent = []
    monkeypatch.setattr(mailer, "_send", lambda *args: sent.append(args))
    mailer.send_order_created({**order, "email": ""}, [])   # 沒有 email 就不寄
    mailer.send_payment_success({**order, "email": ""})
    assert not sent


class FakeSMTP:
    """記錄 smtplib 呼叫順序,避免測試真的連線。"""
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        self.message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.calls.append("quit")
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.message = msg
        self.calls.append("send")


def test_mailer_sends_through_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    for key, value in {"host": "smtp.example.com", "port": 587, "user": "bot@example.com",
                       "password": "secret", "use_tls": True, "use_ssl": False,
                       "sender": "bot@example.com", "sender_name": "美師傅 meishifu"}.items():
        monkeypatch.setitem(config.MAIL, key, value)

    mailer._send("buyer@example.com", "訂單成立", "<p>謝謝</p>")
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.example.com" and smtp.port == 587
    assert smtp.calls == ["starttls", ("login", "bot@example.com", "secret"), "send", "quit"]
    assert smtp.message["To"] == "buyer@example.com"
    # 主旨與寄件人名稱含中文,需以 MIME 編碼夾帶
    subject = str(make_header(decode_header(str(smtp.message["Subject"]))))
    assert subject == "訂單成立"
    assert "bot@example.com" in str(smtp.message["From"])

    # send_async 失敗時只記 log,不會往外拋
    monkeypatch.setattr(mailer, "_send", lambda *_args: (_ for _ in ()).throw(OSError("smtp down")))
    thread_target = []
    monkeypatch.setattr(mailer.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda _s: thread_target.append(target())})())
    mailer.send_async("buyer@example.com", "主旨", "<p>x</p>")
    mailer.send_async("", "主旨", "<p>x</p>")   # 沒有收件者就不開執行緒
    assert thread_target == [None]
