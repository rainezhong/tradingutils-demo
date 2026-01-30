"""Polygon blockchain interactions for Polymarket.

Provides:
- USDC balance checking
- Token approval management
- Gas price estimation
- Transaction monitoring
"""

import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional

from .exceptions import PolymarketBlockchainError


logger = logging.getLogger(__name__)


# Contract addresses on Polygon mainnet
POLYGON_CHAIN_ID = 137
MUMBAI_CHAIN_ID = 80001

CONTRACTS = {
    POLYGON_CHAIN_ID: {
        "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "USDC_E": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # Same as USDC on Polygon
        "CTF_EXCHANGE": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "NEG_RISK_CTF_EXCHANGE": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "NEG_RISK_ADAPTER": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    },
    MUMBAI_CHAIN_ID: {
        "USDC": "0x0FA8781a83E46826621b3BC094Ea2A0212e71B23",
        "CTF_EXCHANGE": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    },
}

# Default RPC endpoints
DEFAULT_RPC_URLS = {
    POLYGON_CHAIN_ID: "https://polygon-rpc.com",
    MUMBAI_CHAIN_ID: "https://rpc-mumbai.maticvigil.com",
}

# ERC20 ABI (minimal for balance and approval)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


class PolygonClient:
    """Client for Polygon blockchain interactions.

    Provides USDC balance checking, approval management, and gas estimation.

    Example:
        >>> from polymarket.wallet import PolymarketWallet
        >>> wallet = PolymarketWallet()
        >>> polygon = PolygonClient(wallet)
        >>> balance = polygon.get_usdc_balance()
        >>> print(f"USDC Balance: ${balance:.2f}")
    """

    def __init__(
        self,
        wallet: Optional[Any] = None,
        rpc_url: Optional[str] = None,
        chain_id: int = POLYGON_CHAIN_ID,
        max_gas_price_gwei: float = 500.0,
    ) -> None:
        """Initialize Polygon client.

        Args:
            wallet: PolymarketWallet instance for signing
            rpc_url: Polygon RPC URL (defaults based on chain_id)
            chain_id: Chain ID (137 for mainnet, 80001 for Mumbai)
            max_gas_price_gwei: Maximum gas price in Gwei (safety limit)
        """
        self._wallet = wallet
        self._chain_id = chain_id
        self._max_gas_price_gwei = max_gas_price_gwei

        # Get RPC URL
        self._rpc_url = (
            rpc_url
            or os.environ.get("POLYGON_RPC_URL")
            or DEFAULT_RPC_URLS.get(chain_id)
        )

        if not self._rpc_url:
            raise PolymarketBlockchainError(f"No RPC URL for chain {chain_id}")

        # Get contract addresses
        self._contracts = CONTRACTS.get(chain_id, {})
        if not self._contracts:
            raise PolymarketBlockchainError(f"No contracts for chain {chain_id}")

        # Initialize web3
        self._web3 = None
        self._usdc_contract = None

        logger.info(
            "PolygonClient initialized: chain=%d, rpc=%s",
            chain_id,
            self._rpc_url[:30] + "...",
        )

    def _get_web3(self):
        """Lazy initialize web3 connection."""
        if self._web3 is None:
            try:
                from web3 import Web3
                from web3.middleware import geth_poa_middleware

                self._web3 = Web3(Web3.HTTPProvider(self._rpc_url))

                # Add PoA middleware for Polygon
                self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)

                if not self._web3.is_connected():
                    raise PolymarketBlockchainError(
                        f"Failed to connect to RPC: {self._rpc_url}"
                    )

            except ImportError:
                raise PolymarketBlockchainError(
                    "web3 package required. Install with: pip install web3"
                )

        return self._web3

    def _get_usdc_contract(self):
        """Get USDC contract instance."""
        if self._usdc_contract is None:
            web3 = self._get_web3()
            usdc_address = self._contracts.get("USDC")
            if not usdc_address:
                raise PolymarketBlockchainError("USDC contract address not found")

            self._usdc_contract = web3.eth.contract(
                address=web3.to_checksum_address(usdc_address),
                abi=ERC20_ABI,
            )

        return self._usdc_contract

    def get_usdc_balance(self, address: Optional[str] = None) -> float:
        """Get USDC balance for an address.

        Args:
            address: Address to check (defaults to wallet address)

        Returns:
            USDC balance as float (already adjusted for 6 decimals)
        """
        if address is None:
            if self._wallet is None:
                raise PolymarketBlockchainError("No wallet or address provided")
            address = self._wallet.address

        web3 = self._get_web3()
        contract = self._get_usdc_contract()

        try:
            balance_raw = contract.functions.balanceOf(
                web3.to_checksum_address(address)
            ).call()

            # USDC has 6 decimals
            balance = float(Decimal(balance_raw) / Decimal(10**6))

            logger.debug("USDC balance for %s: %.2f", address[:10] + "...", balance)
            return balance

        except Exception as e:
            raise PolymarketBlockchainError(f"Failed to get USDC balance: {e}")

    def get_usdc_allowance(
        self,
        spender: str,
        owner: Optional[str] = None,
    ) -> float:
        """Get USDC allowance for a spender.

        Args:
            spender: Address allowed to spend (e.g., CTF Exchange)
            owner: Owner address (defaults to wallet address)

        Returns:
            Allowance as float
        """
        if owner is None:
            if self._wallet is None:
                raise PolymarketBlockchainError("No wallet or owner provided")
            owner = self._wallet.address

        web3 = self._get_web3()
        contract = self._get_usdc_contract()

        try:
            allowance_raw = contract.functions.allowance(
                web3.to_checksum_address(owner),
                web3.to_checksum_address(spender),
            ).call()

            return float(Decimal(allowance_raw) / Decimal(10**6))

        except Exception as e:
            raise PolymarketBlockchainError(f"Failed to get allowance: {e}")

    def check_ctf_exchange_approval(self, min_amount: float = 0) -> bool:
        """Check if CTF Exchange is approved to spend USDC.

        Args:
            min_amount: Minimum required allowance

        Returns:
            True if approved for at least min_amount
        """
        exchange_address = self._contracts.get("CTF_EXCHANGE")
        if not exchange_address:
            raise PolymarketBlockchainError("CTF Exchange address not found")

        allowance = self.get_usdc_allowance(exchange_address)
        return allowance >= min_amount

    def get_gas_price(self) -> Dict[str, float]:
        """Get current gas prices in Gwei.

        Returns:
            Dictionary with 'fast', 'standard', 'slow' prices
        """
        web3 = self._get_web3()

        try:
            # Get base gas price
            gas_price_wei = web3.eth.gas_price
            gas_price_gwei = float(web3.from_wei(gas_price_wei, "gwei"))

            # Polygon typically uses priority fees
            return {
                "slow": gas_price_gwei * 0.8,
                "standard": gas_price_gwei,
                "fast": gas_price_gwei * 1.5,
            }

        except Exception as e:
            raise PolymarketBlockchainError(f"Failed to get gas price: {e}")

    def estimate_gas_cost(
        self,
        gas_limit: int = 100000,
        priority: str = "standard",
    ) -> float:
        """Estimate gas cost in MATIC.

        Args:
            gas_limit: Gas limit for transaction
            priority: 'slow', 'standard', or 'fast'

        Returns:
            Estimated cost in MATIC
        """
        gas_prices = self.get_gas_price()
        gas_price_gwei = gas_prices.get(priority, gas_prices["standard"])

        # Convert to MATIC
        cost_wei = gas_limit * int(gas_price_gwei * 10**9)
        cost_matic = float(Decimal(cost_wei) / Decimal(10**18))

        return cost_matic

    def get_matic_balance(self, address: Optional[str] = None) -> float:
        """Get MATIC balance for gas payments.

        Args:
            address: Address to check (defaults to wallet address)

        Returns:
            MATIC balance as float
        """
        if address is None:
            if self._wallet is None:
                raise PolymarketBlockchainError("No wallet or address provided")
            address = self._wallet.address

        web3 = self._get_web3()

        try:
            balance_wei = web3.eth.get_balance(web3.to_checksum_address(address))
            return float(web3.from_wei(balance_wei, "ether"))

        except Exception as e:
            raise PolymarketBlockchainError(f"Failed to get MATIC balance: {e}")

    def wait_for_transaction(
        self,
        tx_hash: str,
        timeout: int = 120,
        poll_interval: float = 2.0,
    ) -> Dict[str, Any]:
        """Wait for a transaction to be mined.

        Args:
            tx_hash: Transaction hash
            timeout: Timeout in seconds
            poll_interval: Polling interval in seconds

        Returns:
            Transaction receipt
        """
        web3 = self._get_web3()

        try:
            receipt = web3.eth.wait_for_transaction_receipt(
                tx_hash,
                timeout=timeout,
                poll_latency=poll_interval,
            )

            return {
                "status": receipt["status"],
                "block_number": receipt["blockNumber"],
                "gas_used": receipt["gasUsed"],
                "tx_hash": tx_hash,
            }

        except Exception as e:
            raise PolymarketBlockchainError(f"Transaction failed: {e}")

    def get_block_number(self) -> int:
        """Get current block number."""
        web3 = self._get_web3()
        return web3.eth.block_number

    def is_connected(self) -> bool:
        """Check if connected to RPC."""
        try:
            web3 = self._get_web3()
            return web3.is_connected()
        except Exception:
            return False

    @property
    def chain_id(self) -> int:
        """Get chain ID."""
        return self._chain_id

    @property
    def contracts(self) -> Dict[str, str]:
        """Get contract addresses."""
        return self._contracts.copy()
