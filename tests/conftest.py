"""Test fixtures to test out security token activities."""
import logging
import sys

import os
import pytest
from eth_utils import to_wei
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sto.models.implementation import Base
from sto.cli.main import cli
from sto.ethereum.utils import priv_key_to_address

from click.testing import CliRunner
from web3 import Web3, EthereumTesterProvider


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / 'db_file.sql')


@pytest.fixture
def dbsession(db_path):
    """We use sqlite in-memory for testing."""
    # https://docs.sqlalchemy.org/en/latest/dialects/sqlite.html
    url = "sqlite+pysqlite:///" + db_path

    engine = create_engine(url, echo=False)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    return session


@pytest.fixture
def monkey_patch_py_evm_gas_limit():
    from eth_tester.backends.pyevm import main
    main.GENESIS_GAS_LIMIT = 9999999999


@pytest.fixture
def web3_test_provider(monkey_patch_py_evm_gas_limit):
    return EthereumTesterProvider()


@pytest.fixture
def web3(web3_test_provider):
    return Web3(web3_test_provider)


@pytest.fixture
def network(web3_test_provider):
    """Network name to be used in database when run against in-memory test chain."""
    return "testing"


@pytest.fixture()
def logger(caplog):
    # caplog is pytest built in fixtur
    # https://docs.pytest.org/en/latest/logging.html
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger()
    return logger


@pytest.fixture
def sample_csv_file():
    """Sample distribution file for tokens."""
    return os.path.join(os.path.dirname(__file__), "..", "docs", "source", "example-distribution.csv")


@pytest.fixture
def private_key_hex(web3_test_provider, web3):
    """Create a static private key with some ETH balance on it."""
    # accounts = web3_test_provider.ethereum_tester.get_accounts()
    private_key_hex = "3fac35a57e1e2867290ae37d54c5de61d52644b42819ce6af0c5a9c25f4c8005"

    acc_zero = web3_test_provider.ethereum_tester.get_accounts()[0]  # Accounts with pregenerated balance
    address = web3_test_provider.ethereum_tester.add_account(private_key_hex)

    # Start with 333 ETH balance
    web3.eth.sendTransaction({"from": acc_zero, "to": address, "value": to_wei(333, "ether")})
    balance = web3.eth.getBalance(address)
    assert balance > 0
    return private_key_hex


@pytest.fixture
def click_runner():
    return CliRunner()


@pytest.fixture
def monkeypatch_create_web3(monkeypatch, web3):
    from sto.ethereum import (
        utils,
        broadcast,
        issuance,
        distribution,
        status
    )
    monkeypatch.setattr(utils, 'create_web3', lambda _: web3)
    monkeypatch.setattr(broadcast, 'create_web3', lambda _: web3)
    monkeypatch.setattr(issuance, 'create_web3', lambda _: web3)
    monkeypatch.setattr(distribution, 'create_web3', lambda _: web3)
    monkeypatch.setattr(status, 'create_web3', lambda _: web3)


@pytest.fixture
def get_contract_deployed_tx():
    def _get_contract_deployed_tx(dbsession, contract_name):
        from sto.models.implementation import PreparedTransaction
        txs = dbsession.query(PreparedTransaction).all()
        for tx in txs:
            if tx.contract_deployment and tx.contract_name == contract_name:
                return tx
    return _get_contract_deployed_tx

@pytest.fixture
def monkeypatch_get_contract_deployed_tx(monkeypatch, get_contract_deployed_tx):
    """
    This feature is needed becuase jsonb is not supported on travis sqlite
    """
    from sto.ethereum import utils, issuance
    monkeypatch.setattr(utils, 'get_contract_deployed_tx', get_contract_deployed_tx)
    monkeypatch.setattr(issuance, 'get_contract_deployed_tx', get_contract_deployed_tx)


@pytest.fixture
def customer_private_key():
    from eth_keys import KeyAPI
    from eth_utils import int_to_big_endian
    keys = KeyAPI()
    pk_bytes = int_to_big_endian(2).rjust(32, b'\x00')
    private_key = keys.PrivateKey(pk_bytes)
    return private_key


@pytest.fixture
def kyc_contract(
        click_runner,
        dbsession,
        db_path,
        private_key_hex,
        monkeypatch_get_contract_deployed_tx,
        monkeypatch_create_web3,
        get_contract_deployed_tx,
        customer_private_key
):
    result = click_runner.invoke(
        cli,
        [
            '--database-file', db_path,
            '--ethereum-private-key', private_key_hex,
            'kyc-deploy'
        ]
    )
    assert result.exit_code == 0
    tx = get_contract_deployed_tx(dbsession, 'BasicKYC')

    # whitelist customer
    result = click_runner.invoke(
        cli,
        [
            '--database-file', db_path,
            '--ethereum-private-key', private_key_hex,
            '--ethereum-gas-limit', 80000,
            'kyc-manage',
            '--whitelist-address', priv_key_to_address(private_key_hex)
        ]
    )
    assert result.exit_code == 0
    result = click_runner.invoke(
        cli,
        [
            '--database-file', db_path,
            '--ethereum-private-key', private_key_hex,
            '--ethereum-gas-limit', 80000,
            'kyc-manage',
            '--whitelist-address', priv_key_to_address(customer_private_key)
        ]
    )
    assert result.exit_code == 0
    return tx.contract_address

