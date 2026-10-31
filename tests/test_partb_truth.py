"""Part B state reconstruction on a small hand-built CRM: what each checkpoint must contain."""
import partb_crm as P


def rows(*dicts):
    cols = list(dicts[0]) if dicts else ["id"]
    return (cols, [dict(d) for d in dicts])


def fixture():
    lead = lambda i, created, status, conv=None: {"id": f"L{i}", "createddate": created, "status": status,
                                                   "converteddate": conv, "convertedcontactid": "C1" if conv else None,
                                                   "convertedaccountid": "A1" if conv else None, "isconverted": 1 if conv else 0}
    case = lambda i, created, closed, status, owner="U1", prio="Medium": {
        "id": f"#K{i} ", "createddate": created, "closeddate": closed, "status": status, "ownerid": owner,
        "priority": prio, "accountid": "A1"}
    order = lambda i, eff: {"id": f"O{i}", "accountid": "A1", "effectivedate": eff, "status": "Activated"}
    orig = {
        "lead": rows(lead(1, "2022-05-01", "Converted", "2022-06-01"),
                     lead(2, "2022-11-01", "Converted", "2023-02-01"),     # converted after cutoff
                     *[lead(10 + i, "2023-02-01", "New") for i in range(20)]),
        "support_case": rows(case(1, "2022-03-01T10:00:00.000+0000", "2023-05-01T10:00:00.000+0000", "Closed  "),
                             *[case(20 + i, "2022-06-01T10:00:00.000+0000", None, "Working") for i in range(14)]),
        "casehistory__c": rows({"id": "H1", "caseid__c": "K1", "field__c": "Owner Assignment", "oldvalue__c": "U1",
                                "newvalue__c": "U2", "createddate": "2023-03-01T00:00:00.000+0000"}),
        "sales_order": rows(order(1, "2022-12-15"), *[order(10 + i, "2023-02-10") for i in range(8)]),
        "orderitem": rows({"id": "I1", "orderid": "O1", "quantity": "2"}),
        "account": rows({"id": "A1", "name": "Acme"}),
    }
    for t in P.DATED:
        orig.setdefault(t, (["id"], []))
    for t in ("opportunitylineitem", "quotelineitem", "opportunity", "quote"):
        orig.setdefault(t, (["id"], []))
    return orig


def ids(state, table):
    return {P.key(r["id"]) for r in state[table][1]}


def test_cutoff_state_hides_the_future_and_reconstructs_open_records():
    s = P.state_at(fixture(), 0)
    assert ids(s, "lead") == {"L1", "L2"}                       # L10.. arrive after the cutoff
    l2 = next(r for r in s["lead"][1] if r["id"] == "L2")
    assert (l2["status"], l2["converteddate"], l2["isconverted"]) == ("Working", None, 0)
    k1 = next(r for r in s["support_case"][1] if P.key(r["id"]) == "K1")
    assert (k1["status"], k1["closeddate"], k1["ownerid"]) == ("Working", None, "U1")


def test_corrections_land_in_the_batch_that_contains_them():
    s1, s2 = P.state_at(fixture(), 1), P.state_at(fixture(), 2)
    k1 = lambda s: next(r for r in s["support_case"][1] if P.key(r["id"]) == "K1")
    assert k1(s1)["ownerid"] == "U2" and k1(s1)["status"] == "Working"     # reassigned 2023-03-01
    assert k1(s2)["status"].strip() == "Closed"                             # closed 2023-05-01
    assert next(r for r in s1["lead"][1] if r["id"] == "L2")["status"] == "Converted"


def test_backdated_orders_arrive_one_checkpoint_late():
    orig = fixture()
    late = P.delayed_orders(orig)
    assert late and late <= {f"O{10 + i}" for i in range(8)}
    s1, s2 = P.state_at(orig, 1), P.state_at(orig, 2)
    assert not (late & ids(s1, "sales_order"))          # effective in window 1, withheld at checkpoint 1
    assert late <= ids(s2, "sales_order")               # present at checkpoint 2
    assert "O1" in ids(P.state_at(orig, 0), "sales_order") and "I1" in ids(P.state_at(orig, 0), "orderitem")


def test_retracted_leads_disappear_at_their_checkpoint_only():
    orig = fixture()
    gone = {P.key(r["id"]) for r in P.retraction_set(orig)[0]}
    assert gone and gone <= {f"L{10 + i}" for i in range(20)}
    assert gone <= ids(P.state_at(orig, 1), "lead")
    assert not (gone & ids(P.state_at(orig, 2), "lead"))


def test_competing_writes_keep_only_the_last_value():
    orig = fixture()
    writes = P.competing(orig, 1)
    assert writes
    s1 = P.state_at(orig, 1)
    prio = {P.key(r["id"]): r["priority"] for r in s1["support_case"][1]}
    for cid, mid, final in writes:
        assert prio[cid] == final and mid == "Low"
    assert any(final == "High" for _, _, final in writes)          # some escalations persist
    assert any(final == "Medium" for _, _, final in writes)        # and some flip back, net no change


def test_controls_do_not_move():
    orig = fixture()
    pre = lambda k: {P.key(r["id"]) for r in P.state_at(orig, k)["support_case"][1]
                     if r["createddate"] < "2022-12-31"}
    assert pre(0) == pre(3) == pre(5)
