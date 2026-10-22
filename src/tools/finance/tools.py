"""
Finance Tools for Chad

Tools exposed to the assistant for retrieving account balances, net worth,
recent transactions, and investment portfolios via Plaid.
"""

from typing import Any, Dict, Optional
from src.tools.base_tool import BaseTool
from src.tools.finance.manager import FinanceManager


class GetFinancialSummaryTool(BaseTool):
    """
    Get real-time balances, net worth calculation, cash, investments, and debt
    across all linked financial accounts.
    """

    name = "get_financial_summary"
    description = (
        "Retrieve current balances and net worth calculation across all linked bank accounts, "
        "credit cards, and investment brokerages. Shows cash total, debt total, investments total, "
        "and individual accounts."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Fetching financial summary & balances"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": (
                    "Finance connector is not configured. Configure Plaid Client ID and Secret "
                    "under Settings -> Connectors -> Finance or ask Chad to set it up."
                ),
            }
        return mgr.get_balances()


class GetRecentTransactionsTool(BaseTool):
    """
    Query recent transactions across linked bank accounts and credit cards,
    with pre-computed spending breakdowns by category, top merchants, and transfer filtering.
    """

    name = "get_recent_transactions"
    description = (
        "Query recent transactions and pre-computed spending breakdown by category and top merchants "
        "across linked bank accounts and credit cards. Useful for budgeting, auditing spending, finding recent purchases, "
        "or reviewing recurring bills. Automatically excludes internal transfers by default."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        days = params.get("days", 30)
        return f"Fetching transactions & spending breakdown for past {days} days"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days in the past to search for transactions (default: 30).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Maximum number of transactions to return (default: 50).",
                    },
                    "exclude_transfers": {
                        "type": "boolean",
                        "description": "Whether to filter out internal transfers and account-to-account payments to focus on actual spending (default: true).",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g. 'Food', 'Travel', 'Gas').",
                    },
                    "institution": {
                        "type": "string",
                        "description": "Optional institution filter (e.g. 'SoFi' or 'Capital One').",
                    },
                },
                "required": [],
            },
        }

    async def execute(
        self,
        days: int = 30,
        count: int = 50,
        exclude_transfers: bool = True,
        category: Optional[str] = None,
        institution: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.get_recent_transactions(
            days=days,
            count=count,
            exclude_transfers=exclude_transfers,
            category=category,
            institution=institution,
        )


class GetInvestmentPortfolioTool(BaseTool):
    """
    Query investment holdings, tickers, quantities, and market values across linked brokerage accounts.
    """

    name = "get_investment_portfolio"
    description = (
        "Query investment holdings, asset allocations, stock/ETF positions, quantities, and market "
        "values across linked brokerage and retirement accounts (e.g. Fidelity)."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Fetching investment portfolio holdings"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.get_investment_portfolio()


class CreateFinanceLinkTokenTool(BaseTool):
    """
    Generate a Plaid Link token to allow the user to link a bank account (Capital One, SoFi, Fidelity, etc.).
    """

    name = "create_finance_link_token"
    description = (
        "Generate a temporary Plaid Link token to launch the bank connection modal in the UI. "
        "Call this when the user asks to link a new bank account or reconnect an existing institution."
    )
    category = "custom"
    is_side_effecting = True
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Creating bank link session"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Set Plaid keys first in Settings -> Connectors -> Finance.",
            }
        return mgr.create_link_token()


class GetRecurringSubscriptionsTool(BaseTool):
    """
    Retrieve active recurring bills, subscriptions, and regular income streams across linked bank accounts.
    """

    name = "get_recurring_subscriptions"
    description = (
        "Retrieve detected recurring bills, subscriptions (e.g. Netflix, Spotify, gym, rent, utility bills), "
        "and regular inflow income streams across linked accounts with frequencies, average amounts, and dates."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Auditing recurring bills and subscriptions"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.get_recurring_transactions()


class GetLiabilitiesTool(BaseTool):
    """
    Retrieve debt and liability details across linked credit cards, mortgages, and student loans.
    """

    name = "get_liabilities"
    description = (
        "Retrieve debt details across linked credit cards, mortgages, and student loans. "
        "Includes upcoming payment due dates, minimum payments due, statement balances, APR percentages, and overdue status."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Checking credit card due dates, balances, and APRs"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.get_liabilities()


class GetInvestmentTransactionsTool(BaseTool):
    """
    Retrieve historical investment transactions across linked brokerage and retirement accounts.
    """

    name = "get_investment_transactions"
    description = (
        "Retrieve historical investment transactions across linked brokerage and retirement accounts (e.g. Fidelity). "
        "Shows buy/sell trades, dividend distributions, fees, deposits, and cash transfers."
    )
    category = "custom"
    is_side_effecting = False
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        days = params.get("days", 30)
        return f"Fetching investment trades & dividends for past {days} days"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days in the past to query transactions (default: 30).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Maximum number of transactions to return (default: 50).",
                    },
                },
                "required": [],
            },
        }

    async def execute(self, days: int = 30, count: int = 50, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.get_investment_transactions(days=days, count=count)


class RefreshFinancialDataTool(BaseTool):
    """
    Request on-demand extraction from Plaid to refresh transactions and investments data.
    """

    name = "refresh_financial_data"
    description = (
        "Request an on-demand extraction from Plaid to refresh transactions and investments data from linked institutions."
    )
    category = "custom"
    is_side_effecting = True
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        return "Refreshing live bank transactions & investments"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        mgr = FinanceManager.get_instance()
        if not mgr.is_configured():
            return {
                "success": False,
                "error": "Finance connector is not configured. Configure it in Settings -> Connectors -> Finance.",
            }
        return mgr.refresh_financial_data()

