"""One-time, idempotent at-rest encryption upgrade for marketplace tokens."""
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import SessionLocal
from app.models.models import MarketplaceAccount


def main() -> None:
    with SessionLocal() as db:
        accounts = db.execute(select(MarketplaceAccount)).scalars().all()
        for account in accounts:
            # TypeDecorator presents legacy values as plaintext; flagging forces
            # the encrypted bind transform without logging a secret.
            flag_modified(account, "access_token")
            if account.refresh_token:
                flag_modified(account, "refresh_token")
        db.commit()
        print({"encrypted_accounts": len(accounts)})


if __name__ == "__main__":
    main()
