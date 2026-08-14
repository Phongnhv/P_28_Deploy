import re
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy import text

db_url = "sqlite:///c:/DATA/P-028/steward_local.db"
connect_args = {"check_same_thread": False}

print("Creating SQLAlchemy engine...")
engine = create_engine(db_url, connect_args=connect_args)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    print("Event connect fired.")
    
    # 1. Register function FIRST
    def _sqlite_regexp(expr, item):
        if item is None:
            return False
        return re.search(expr, str(item)) is not None
        
    try:
        dbapi_conn.create_function("REGEXP", 2, _sqlite_regexp)
        print("REGEXP function created successfully.")
    except Exception as e:
        print("Failed to create REGEXP function:")
        import traceback
        traceback.print_exc()
        raise e

    # 2. Run WAL pragma and close cursor SECOND
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    print("WAL pragma executed.")
    cursor.close()

try:
    with Session(engine) as session:
        session.execute(text("SELECT 1"))
    print("Success!")
except Exception as e:
    pass
