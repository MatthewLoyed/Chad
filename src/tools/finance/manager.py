"""
Finance Manager (Plaid Integration)

Generic infrastructure for linking financial accounts via Plaid,
managing stored institution tokens, and retrieving balances, transactions,
and investment portfolios.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("FinanceManager")

_INSTANCE: Optional[FinanceManager] = None


def get_accounts_file_path() -> Path:
    """Return the absolute path to the local finance accounts storage file."""
    return get_project_root() / "config" / "finance_accounts.json"


class FinanceManager:
    """
    Singleton manager for Plaid API integration and account token storage.
    """

    def __init__(self):
        self._accounts_file = get_accounts_file_path()

    @classmethod
    def get_instance(cls) -> FinanceManager:
        global _INSTANCE
        if _INSTANCE is None:
            _INSTANCE = cls()
        return _INSTANCE

    @classmethod
    def reset_instance(cls) -> None:
        global _INSTANCE
        _INSTANCE = None

    def is_configured(self) -> bool:
        """True if Plaid client ID and secret are set."""
        client_id = ConfigManager.get_secret("PLAID_CLIENT_ID") or ConfigManager.get_secret("plaid.client_id")
        secret = ConfigManager.get_secret("PLAID_SECRET") or ConfigManager.get_secret("plaid.secret")
        if not client_id or not secret:
            return False
        from src.infrastructure.connectors.policy import _is_placeholder
        return not _is_placeholder(str(client_id)) and not _is_placeholder(str(secret))

    def get_environment_name(self) -> str:
        """Return the configured Plaid environment (sandbox or production)."""
        env = ConfigManager.get_secret("PLAID_ENV") or ConfigManager.get_secret("plaid.env") or "sandbox"
        return str(env).strip().lower()

    def get_api_client(self):
        """Construct an authenticated PlaidApi instance."""
        import plaid
        from plaid.api import plaid_api

        client_id = ConfigManager.get_secret("PLAID_CLIENT_ID") or ConfigManager.get_secret("plaid.client_id")
        secret = ConfigManager.get_secret("PLAID_SECRET") or ConfigManager.get_secret("plaid.secret")
        if not client_id or not secret:
            raise ValueError("Plaid is not configured. Set PLAID_CLIENT_ID and PLAID_SECRET in Settings -> Connectors -> Finance.")

        env = self.get_environment_name()
        host_env = plaid.Environment.Sandbox
        if env in ("production", "prod"):
            host_env = plaid.Environment.Production
        elif env in ("development", "dev"):
            host_env = getattr(plaid.Environment, "Development", plaid.Environment.Production)

        configuration = plaid.Configuration(
            host=host_env,
            api_key={"clientId": str(client_id).strip(), "secret": str(secret).strip()},
        )
        api_client = plaid.ApiClient(configuration)
        return plaid_api.PlaidApi(api_client)

    # ── Storage Helpers ─────────────────────────────────────────────────────────

    def _load_storage(self) -> Dict[str, Any]:
        """Load stored accounts data from disk."""
        if not self._accounts_file.exists():
            return {"items": {}}
        try:
            with open(self._accounts_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read finance accounts file: {e}")
            return {"items": {}}

    def _save_storage(self, data: Dict[str, Any]) -> None:
        """Write stored accounts data to disk atomically."""
        self._accounts_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._accounts_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self._accounts_file)
        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink()
            raise e

    # ── Plaid Link Handshake ───────────────────────────────────────────────────

    def create_link_token(self, client_user_id: str = "chad-user") -> Dict[str, Any]:
        """
        Generate a link_token for initializing the Plaid Link webview / modal.
        """
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.products import Products
        from plaid.model.country_code import CountryCode

        client = self.get_api_client()

        # Include transactions and investments products
        products = [Products("transactions")]
        optional_products = [Products("investments"), Products("liabilities")]

        request = LinkTokenCreateRequest(
            products=products,
            optional_products=optional_products,
            client_name="Chad Assistant",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id=client_user_id),
        )

        try:
            response = client.link_token_create(request)
            return {
                "success": True,
                "link_token": response.link_token,
                "expiration": str(response.expiration),
            }
        except Exception as e:
            logger.error(f"Failed to create Plaid link token: {e}")
            return {"success": False, "error": str(e)}

    def exchange_public_token(
        self,
        public_token: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Exchange public_token received from Plaid Link for a permanent access_token.
        """
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
        from plaid.model.accounts_get_request import AccountsGetRequest

        client = self.get_api_client()
        metadata = metadata or {}

        try:
            exchange_req = ItemPublicTokenExchangeRequest(public_token=public_token)
            exchange_res = client.item_public_token_exchange(exchange_req)

            access_token = exchange_res.access_token
            item_id = exchange_res.item_id

            # Fetch account names/masks to store metadata
            institution_name = metadata.get("institution", {}).get("name") or "Linked Bank"
            institution_id = metadata.get("institution", {}).get("institution_id") or ""

            accounts_req = AccountsGetRequest(access_token=access_token)
            accounts_res = client.accounts_get(accounts_req)

            accounts_list = []
            for acc in accounts_res.accounts:
                accounts_list.append({
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "official_name": acc.official_name,
                    "mask": acc.mask,
                    "type": str(acc.type),
                    "subtype": str(acc.subtype) if acc.subtype else "",
                })

            storage = self._load_storage()
            storage["items"][item_id] = {
                "access_token": access_token,
                "item_id": item_id,
                "institution_id": institution_id,
                "institution_name": institution_name,
                "linked_at": datetime.utcnow().isoformat(),
                "accounts": accounts_list,
            }
            self._save_storage(storage)

            return {
                "success": True,
                "item_id": item_id,
                "institution_name": institution_name,
                "account_count": len(accounts_list),
            }
        except Exception as e:
            logger.error(f"Failed to exchange public token: {e}")
            return {"success": False, "error": str(e)}

    def list_linked_institutions(self) -> List[Dict[str, Any]]:
        """
        List connected institutions and their accounts without exposing secrets.
        """
        storage = self._load_storage()
        results = []
        for item_id, item_data in storage.get("items", {}).items():
            results.append({
                "item_id": item_id,
                "institution_name": item_data.get("institution_name", "Unknown Institution"),
                "institution_id": item_data.get("institution_id", ""),
                "linked_at": item_data.get("linked_at", ""),
                "accounts": item_data.get("accounts", []),
            })
        return results

    def disconnect_institution(self, item_id: str) -> Dict[str, Any]:
        """
        Revoke Plaid item access and delete from local storage.
        """
        storage = self._load_storage()
        item = storage.get("items", {}).get(item_id)
        if not item:
            return {"success": False, "error": f"Item '{item_id}' not found"}

        access_token = item.get("access_token")
        if access_token:
            try:
                from plaid.model.item_remove_request import ItemRemoveRequest
                client = self.get_api_client()
                client.item_remove(ItemRemoveRequest(access_token=access_token))
            except Exception as e:
                logger.warning(f"Plaid item_remove call failed (proceeding with local removal): {e}")

        del storage["items"][item_id]
        self._save_storage(storage)
        return {"success": True, "message": f"Institution '{item.get('institution_name')}' disconnected."}

    # ── Data Ingestion & Queries ───────────────────────────────────────────────

    def get_balances(self) -> Dict[str, Any]:
        """
        Fetch real-time balances across all linked institutions and calculate net worth.
        """
        from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {
                "success": True,
                "net_worth": 0.0,
                "total_cash": 0.0,
                "total_investments": 0.0,
                "total_debt": 0.0,
                "accounts": [],
                "message": "No financial accounts linked yet.",
            }

        client = self.get_api_client()
        total_cash = 0.0
        total_investments = 0.0
        total_debt = 0.0
        all_accounts = []

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Bank")
            try:
                req = AccountsBalanceGetRequest(access_token=access_token)
                res = client.accounts_balance_get(req)
                for acc in res.accounts:
                    current = acc.balances.current or 0.0
                    available = acc.balances.available
                    acc_type = str(acc.type).lower()
                    acc_subtype = str(acc.subtype).lower() if acc.subtype else ""

                    if acc_type == "depository":
                        total_cash += current
                    elif acc_type == "investment":
                        total_investments += current
                    elif acc_type in ("credit", "loan"):
                        total_debt += current

                    all_accounts.append({
                        "institution": inst_name,
                        "account_id": acc.account_id,
                        "name": acc.name,
                        "mask": acc.mask,
                        "type": acc_type,
                        "subtype": acc_subtype,
                        "current_balance": current,
                        "available_balance": available,
                        "currency": acc.balances.iso_currency_code or "USD",
                    })
            except Exception as e:
                logger.error(f"Failed to fetch balance for item {item_id} ({inst_name}): {e}")

        net_worth = (total_cash + total_investments) - total_debt
        return {
            "success": True,
            "net_worth": round(net_worth, 2),
            "total_cash": round(total_cash, 2),
            "total_investments": round(total_investments, 2),
            "total_debt": round(total_debt, 2),
            "accounts": all_accounts,
        }

    def get_recent_transactions(
        self,
        days: int = 30,
        count: int = 50,
        exclude_transfers: bool = True,
        category: Optional[str] = None,
        institution: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sync recent transactions across linked accounts with pre-computed spending
        breakdowns by category and top merchants.
        """
        from plaid.model.transactions_sync_request import TransactionsSyncRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "transactions": [], "spending_by_category": {}, "top_merchants": [], "message": "No financial accounts linked."}

        client = self.get_api_client()
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        all_tx = []
        spending_by_category: Dict[str, float] = {}
        merchant_spend: Dict[str, Dict[str, Any]] = {}
        total_spending = 0.0
        total_income = 0.0
        transfers_total = 0.0

        target_inst = institution.lower().strip() if institution else None
        target_cat = category.lower().strip() if category else None

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Bank")

            if target_inst and target_inst not in inst_name.lower():
                continue

            try:
                req = TransactionsSyncRequest(access_token=access_token)
                res = client.transactions_sync(req)
                for tx in getattr(res, "added", []):
                    tx_date = str(tx.date)
                    if tx_date < cutoff_date:
                        continue

                    raw_name = tx.name or "Transaction"
                    merchant = getattr(tx, "merchant_name", None) or raw_name
                    amount = float(tx.amount) if tx.amount is not None else 0.0

                    # Extract category
                    pfc = getattr(tx, "personal_finance_category", None)
                    if pfc and getattr(pfc, "primary", None):
                        cat_str = str(pfc.primary).replace("_", " ").title()
                    elif tx.category and len(tx.category) > 0:
                        cat_str = tx.category[0]
                    else:
                        cat_str = "General Merchandise"

                    # Identify internal transfers / card payoffs / deposits
                    cat_lower = cat_str.lower()
                    name_lower = raw_name.lower()
                    is_transfer = (
                        "transfer" in cat_lower
                        or "loan payment" in cat_lower
                        or "credit card payment" in cat_lower
                        or any(kw in name_lower for kw in (
                            "transfer to", "transfer from", "sofi vault", "autopay",
                            "payment to", "credit card payment", "ach transfer",
                            "deposit to", "wire transfer", "payment thank you"
                        ))
                    )

                    if is_transfer:
                        transfers_total += abs(amount)
                        if exclude_transfers:
                            continue

                    # Filter by category if requested
                    if target_cat and target_cat not in cat_lower and target_cat not in name_lower:
                        continue

                    # In Plaid: positive amount = expense/debit, negative amount = credit/deposit
                    if amount > 0:
                        total_spending += amount
                        spending_by_category[cat_str] = spending_by_category.get(cat_str, 0.0) + amount

                        m_key = merchant.strip().title()
                        if m_key not in merchant_spend:
                            merchant_spend[m_key] = {"merchant": m_key, "total": 0.0, "count": 0}
                        merchant_spend[m_key]["total"] += amount
                        merchant_spend[m_key]["count"] += 1
                    else:
                        total_income += abs(amount)

                    record = {
                        "date": tx_date,
                        "merchant": merchant,
                        "amount": round(amount, 2),
                        "category": cat_str,
                        "institution": inst_name,
                    }
                    if getattr(tx, "pending", False):
                        record["pending"] = True

                    all_tx.append(record)
            except Exception as e:
                logger.error(f"Failed to sync transactions for item {item_id}: {e}")

        # Sort transactions descending by date
        all_tx.sort(key=lambda x: x["date"], reverse=True)

        # Sort spending by category descending
        sorted_categories = dict(sorted(spending_by_category.items(), key=lambda item: item[1], reverse=True))
        sorted_categories = {k: round(v, 2) for k, v in sorted_categories.items()}

        # Sort top merchants descending by total spend
        sorted_merchants = sorted(merchant_spend.values(), key=lambda m: m["total"], reverse=True)
        top_merchants = [
            {"merchant": m["merchant"], "total_spent": round(m["total"], 2), "transactions": m["count"]}
            for m in sorted_merchants[:10]
        ]

        return {
            "success": True,
            "days_analyzed": days,
            "total_spending": round(total_spending, 2),
            "total_income": round(total_income, 2),
            "transfers_total": round(transfers_total, 2),
            "spending_by_category": sorted_categories,
            "top_merchants": top_merchants,
            "transaction_count": len(all_tx[:count]),
            "transactions": all_tx[:count],
        }

    def get_investment_portfolio(self) -> Dict[str, Any]:
        """
        Fetch investment holdings, securities, and asset allocation across linked brokerages.
        """
        from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "holdings": [], "message": "No financial accounts linked."}

        client = self.get_api_client()
        all_holdings = []
        total_value = 0.0

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Brokerage")
            try:
                req = InvestmentsHoldingsGetRequest(access_token=access_token)
                res = client.investments_holdings_get(req)

                securities_map = {s.security_id: s for s in res.securities}

                for holding in res.holdings:
                    sec = securities_map.get(holding.security_id)
                    val = holding.institution_value or 0.0
                    total_value += val
                    all_holdings.append({
                        "institution": inst_name,
                        "ticker": sec.ticker_symbol if sec else "N/A",
                        "name": sec.name if sec else "Unknown Security",
                        "security_type": sec.type if sec else "other",
                        "quantity": holding.quantity,
                        "institution_value": val,
                        "cost_basis": holding.cost_basis,
                        "currency": holding.iso_currency_code or "USD",
                    })
            except Exception as e:
                logger.warning(f"Item {item_id} does not have investments or holdings error: {e}")

        return {
            "success": True,
            "total_portfolio_value": round(total_value, 2),
            "holdings": all_holdings,
        }

    def get_recurring_transactions(self) -> Dict[str, Any]:
        """
        Fetch recurring inflow streams (paychecks/deposits) and outflow streams
        (subscriptions/recurring bills) across all linked accounts.
        """
        from plaid.model.transactions_recurring_get_request import TransactionsRecurringGetRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "inflow_streams": [], "outflow_streams": [], "message": "No financial accounts linked."}

        client = self.get_api_client()
        inflow_streams = []
        outflow_streams = []
        monthly_recurring_total = 0.0

        def _extract_amount(val: Any) -> float:
            if hasattr(val, "amount") and val.amount is not None:
                return float(val.amount)
            try:
                return float(val)
            except Exception:
                return 0.0

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Bank")
            try:
                req = TransactionsRecurringGetRequest(access_token=access_token)
                res = client.transactions_recurring_get(req)

                for stream in getattr(res, "outflow_streams", []):
                    amt = _extract_amount(getattr(stream, "average_amount", 0.0))
                    freq = str(getattr(stream, "frequency", "MONTHLY")).upper()
                    # Normalize to estimated monthly amount
                    mult = 1.0
                    if "WEEK" in freq and "BI" not in freq:
                        mult = 4.33
                    elif "BI" in freq:
                        mult = 2.16
                    elif "ANNUAL" in freq or "YEAR" in freq:
                        mult = 1.0 / 12.0
                    monthly_recurring_total += amt * mult

                    outflow_streams.append({
                        "institution": inst_name,
                        "stream_id": getattr(stream, "stream_id", ""),
                        "account_id": getattr(stream, "account_id", ""),
                        "merchant_name": getattr(stream, "merchant_name", "") or getattr(stream, "description", "Subscription"),
                        "description": getattr(stream, "description", ""),
                        "category": getattr(stream, "category", []) or [],
                        "frequency": freq,
                        "average_amount": round(amt, 2),
                        "last_amount": round(_extract_amount(getattr(stream, "last_amount", 0.0)), 2),
                        "first_date": str(getattr(stream, "first_date", "")),
                        "last_date": str(getattr(stream, "last_date", "")),
                        "is_active": getattr(stream, "is_active", True),
                        "status": str(getattr(stream, "status", "active")),
                    })

                for stream in getattr(res, "inflow_streams", []):
                    amt = _extract_amount(getattr(stream, "average_amount", 0.0))
                    freq = str(getattr(stream, "frequency", "MONTHLY")).upper()
                    inflow_streams.append({
                        "institution": inst_name,
                        "stream_id": getattr(stream, "stream_id", ""),
                        "account_id": getattr(stream, "account_id", ""),
                        "merchant_name": getattr(stream, "merchant_name", "") or getattr(stream, "description", "Income"),
                        "description": getattr(stream, "description", ""),
                        "category": getattr(stream, "category", []) or [],
                        "frequency": freq,
                        "average_amount": round(amt, 2),
                        "last_amount": round(_extract_amount(getattr(stream, "last_amount", 0.0)), 2),
                        "first_date": str(getattr(stream, "first_date", "")),
                        "last_date": str(getattr(stream, "last_date", "")),
                        "is_active": getattr(stream, "is_active", True),
                        "status": str(getattr(stream, "status", "active")),
                    })
            except Exception as e:
                logger.warning(f"Failed to fetch recurring transactions for item {item_id}: {e}")

        return {
            "success": True,
            "estimated_monthly_recurring_bills": round(monthly_recurring_total, 2),
            "outflow_subscriptions_and_bills": outflow_streams,
            "inflow_income_streams": inflow_streams,
        }

    def get_liabilities(self) -> Dict[str, Any]:
        """
        Fetch debt details across credit cards, student loans, and mortgages,
        including upcoming payment due dates, minimum payments, APRs, and balances.
        """
        from plaid.model.liabilities_get_request import LiabilitiesGetRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "credit": [], "mortgage": [], "student": [], "message": "No financial accounts linked."}

        client = self.get_api_client()
        all_credit = []
        all_mortgage = []
        all_student = []

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Bank")
            try:
                req = LiabilitiesGetRequest(access_token=access_token)
                res = client.liabilities_get(req)
                acc_map = {a.account_id: a for a in getattr(res, "accounts", [])}
                liab = getattr(res, "liabilities", None)
                if not liab:
                    continue

                # Credit cards
                for card in getattr(liab, "credit", []) or []:
                    acc = acc_map.get(card.account_id)
                    aprs = []
                    for apr in getattr(card, "aprs", []) or []:
                        aprs.append({
                            "percentage": apr.apr_percentage,
                            "type": apr.apr_type,
                            "balance_subject_to_apr": apr.balance_subject_to_apr,
                        })

                    all_credit.append({
                        "institution": inst_name,
                        "account_name": acc.name if acc else "Credit Card",
                        "mask": acc.mask if acc else None,
                        "account_id": card.account_id,
                        "is_overdue": getattr(card, "is_overdue", False),
                        "last_statement_balance": getattr(card, "last_statement_balance", None),
                        "minimum_payment_amount": getattr(card, "minimum_payment_amount", None),
                        "next_payment_due_date": str(getattr(card, "next_payment_due_date", "") or ""),
                        "last_payment_amount": getattr(card, "last_payment_amount", None),
                        "last_payment_date": str(getattr(card, "last_payment_date", "") or ""),
                        "aprs": aprs,
                    })

                # Mortgages
                for mort in getattr(liab, "mortgage", []) or []:
                    acc = acc_map.get(mort.account_id)
                    all_mortgage.append({
                        "institution": inst_name,
                        "account_name": acc.name if acc else "Mortgage",
                        "mask": acc.mask if acc else None,
                        "account_id": mort.account_id,
                        "interest_rate_percentage": getattr(getattr(mort, "interest_rate", None), "percentage", None),
                        "next_monthly_payment": getattr(mort, "next_monthly_payment", None),
                        "next_payment_due_date": str(getattr(mort, "next_payment_due_date", "") or ""),
                        "origination_principal_amount": getattr(mort, "origination_principal_amount", None),
                        "origination_date": str(getattr(mort, "origination_date", "") or ""),
                    })

                # Student loans
                for loan in getattr(liab, "student", []) or []:
                    acc = acc_map.get(loan.account_id)
                    all_student.append({
                        "institution": inst_name,
                        "account_name": acc.name if acc else "Student Loan",
                        "mask": acc.mask if acc else None,
                        "account_id": loan.account_id,
                        "loan_name": getattr(loan, "loan_name", ""),
                        "interest_rate_percentage": getattr(loan, "interest_rate_percentage", None),
                        "minimum_payment_amount": getattr(loan, "minimum_payment_amount", None),
                        "next_payment_due_date": str(getattr(loan, "next_payment_due_date", "") or ""),
                        "expected_payoff_date": str(getattr(loan, "expected_payoff_date", "") or ""),
                        "loan_status": getattr(getattr(loan, "loan_status", None), "type", "active"),
                    })
            except Exception as e:
                logger.warning(f"Item {item_id} does not support liabilities or error: {e}")

        return {
            "success": True,
            "credit_cards": all_credit,
            "mortgages": all_mortgage,
            "student_loans": all_student,
        }

    def get_investment_transactions(self, days: int = 30, count: int = 50) -> Dict[str, Any]:
        """
        Fetch investment transactions (buys, sells, dividends, transfers)
        across linked brokerage accounts.
        """
        from datetime import date
        from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "transactions": [], "message": "No financial accounts linked."}

        client = self.get_api_client()
        today = date.today()
        start = today - timedelta(days=days)
        all_tx = []

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            inst_name = item_data.get("institution_name", "Brokerage")
            try:
                req = InvestmentsTransactionsGetRequest(
                    access_token=access_token,
                    start_date=start,
                    end_date=today,
                )
                res = client.investments_transactions_get(req)
                sec_map = {s.security_id: s for s in getattr(res, "securities", [])}

                for tx in getattr(res, "investment_transactions", []):
                    sec = sec_map.get(tx.security_id)
                    all_tx.append({
                        "institution": inst_name,
                        "account_id": tx.account_id,
                        "date": str(tx.date),
                        "name": tx.name,
                        "ticker": sec.ticker_symbol if sec else "N/A",
                        "security_name": sec.name if sec else tx.name,
                        "security_type": sec.type if sec else "other",
                        "type": str(tx.type),
                        "subtype": str(tx.subtype) if tx.subtype else "",
                        "quantity": tx.quantity,
                        "price": tx.price,
                        "amount": tx.amount,
                        "fees": tx.fees,
                        "currency": tx.iso_currency_code or "USD",
                    })
            except Exception as e:
                logger.warning(f"Item {item_id} does not support investments transactions or error: {e}")

        all_tx.sort(key=lambda x: x["date"], reverse=True)
        return {
            "success": True,
            "count": len(all_tx[:count]),
            "investment_transactions": all_tx[:count],
        }

    def refresh_financial_data(self) -> Dict[str, Any]:
        """
        Request on-demand extraction from Plaid to refresh transactions and investments data.
        """
        from plaid.model.transactions_refresh_request import TransactionsRefreshRequest
        from plaid.model.investments_refresh_request import InvestmentsRefreshRequest

        storage = self._load_storage()
        items = storage.get("items", {})
        if not items:
            return {"success": True, "refreshed_items": 0, "message": "No financial accounts linked."}

        client = self.get_api_client()
        refreshed = 0

        for item_id, item_data in items.items():
            access_token = item_data.get("access_token")
            try:
                req_tx = TransactionsRefreshRequest(access_token=access_token)
                client.transactions_refresh(req_tx)
                refreshed += 1
            except Exception as e:
                logger.debug(f"Transactions refresh skipped for {item_id}: {e}")

            try:
                req_inv = InvestmentsRefreshRequest(access_token=access_token)
                client.investments_refresh(req_inv)
            except Exception as e:
                logger.debug(f"Investments refresh skipped for {item_id}: {e}")

        return {
            "success": True,
            "refreshed_items": refreshed,
            "message": f"Successfully triggered data refresh for {refreshed} connected institution(s).",
        }

