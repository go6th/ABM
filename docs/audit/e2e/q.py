import sys, psycopg
def q(sql, args=None):
    c = psycopg.connect("postgresql://postgres:dev@localhost:5432/ai_e2e")
    cur = c.execute(sql, args)
    rows = cur.fetchall() if cur.description else []
    return [tuple(str(x) if not isinstance(x,(int,float,type(None),bool)) else x for x in r) for r in rows]
if __name__ == "__main__":
    for r in q(sys.argv[1]): print(r)
