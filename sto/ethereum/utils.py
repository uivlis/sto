import json
import os

from typing import Optional

import rlp
from eth_abi import encode_abi
from web3 import Web3, HTTPProvider
from web3.contract import Contract
from web3.utils.abi import get_constructor_abi, merge_args_and_kwargs
from web3.utils.events import get_event_data
from web3.utils.filters import construct_event_filter_params
from eth_utils import keccak, to_checksum_address, to_bytes, is_hex_address, is_checksum_address
from web3.utils.contracts import encode_abi

from sto.cli.main import is_ethereum_network


class NoNodeConfigured(Exception):
    pass


class NeedPrivateKey(Exception):
    pass



def check_good_node_url(node_url: str):
    if not node_url:
        raise NoNodeConfigured("You need to give --ethereum-node-url command line option or set it up in a config file")


def check_good_private_key(private_key_hex: str):
    if not private_key_hex:
        raise NeedPrivateKey("You need to give --ethereum-private-key command line option or set it up in a config file")


def get_abi(abi_file: Optional[str]):

    if not abi_file:
        # Use built-in solc output drop
        abi_file = os.path.join(os.path.dirname(__file__), "contracts-flattened.json")

    with open(abi_file, "rt") as inp:
        return json.load(inp)


def create_web3(url: str) -> Web3:
    """Web3 initializer."""

    if isinstance(url, Web3):
        # Shortcut for testing
        return url
    else:
        return Web3(HTTPProvider(url))



def mk_contract_address(sender: str, nonce: int) -> str:
    """Create a contract address using eth-utils.

    https://ethereum.stackexchange.com/a/761/620
    """
    sender_bytes = to_bytes(hexstr=sender)
    raw = rlp.encode([sender_bytes, nonce])
    h = keccak(raw)
    address_bytes = h[12:]
    return to_checksum_address(address_bytes)


# Sanity check
assert mk_contract_address(to_checksum_address("0x6ac7ea33f8831ea9dcc53393aaa88b25a785dbf0"), 1) == to_checksum_address("0x343c43a37d37dff08ae8c4a11544c718abb4fcf8")


def validate_ethereum_address(address: str):
    """Clever Ethereum address validator.

    Assume all lowercase addresses are not checksummed.
    """

    if len(address) < 42:
        raise ValueError("Not an Ethereum address: {}".format(address))

    try:
        if not is_hex_address(address):
            raise ValueError("Not an Ethereum address: {}".format(address))
    except UnicodeEncodeError:
        raise ValueError("Could not decode: {}".format(address))

    # Check if checksummed address if any of the letters is upper case
    if any([c.isupper() for c in address]):
        if not is_checksum_address(address):
            raise ValueError("Not a checksummed Ethereum address: {}".format(address))


def get_constructor_arguments(contract: Contract, args: Optional[list]=None, kwargs: Optional[dict]=None):
    """Get constructor arguments for Etherscan verify.

    https://etherscanio.freshdesk.com/support/solutions/articles/16000053599-contract-verification-constructor-arguments
    """

    # return contract._encode_constructor_data(args=args, kwargs=kwargs)

    constructor_abi = get_constructor_abi(contract.abi)

    if args is not None:
        return contract._encode_abi(constructor_abi, args)[2:]  # No 0x
    else:
        constructor_abi = get_constructor_abi(contract.abi)
        kwargs = kwargs or {}
        arguments = merge_args_and_kwargs(constructor_abi, [], kwargs)
        # deploy_data = add_0x_prefix(
        #    contract._encode_abi(constructor_abi, arguments)
        #)

        # TODO: Looks like recent Web3.py ABI change
        deploy_data = encode_abi(contract.web3, constructor_abi, arguments)
        return deploy_data


def getLogs(self,
    argument_filters=None,
    fromBlock=None,
    toBlock="latest",
    address=None,
    topics=None):
    """Get events using eth_getLogs API.

    This is a stateless method, as opposite to createFilter.
    It can be safely called against nodes which do not provide eth_newFilter API, like Infura.

    :param argument_filters:
    :param fromBlock:
    :param toBlock:
    :param address:
    :param topics:
    :return:
    """

    if fromBlock is None:
        raise TypeError("Missing mandatory keyword argument to getLogs: fromBlock")

    abi = self._get_event_abi()

    argument_filters = dict()

    _filters = dict(**argument_filters)

    # Construct JSON-RPC raw filter presentation based on human readable Python descriptions
    # Namely, convert event names to their keccak signatures
    data_filter_set, event_filter_params = construct_event_filter_params(
        abi,
        contract_address=self.address,
        argument_filters=_filters,
        fromBlock=fromBlock,
        toBlock=toBlock,
        address=address,
        topics=topics,
    )

    # Call JSON-RPC API
    logs = self.web3.eth.getLogs(event_filter_params)

    # Convert raw binary data to Python proxy objects as described by ABI
    for entry in logs:
        yield get_event_data(abi, entry)


def deploy_contract(
        network,
        dbsession,
        ethereum_abi_file,
        ethereum_private_key,
        ethereum_node_url,
        ethereum_gas_limit,
        ethereum_gas_price,
        contract_name,
        contructor_args=()
):
    from sto.ethereum.txservice import EthereumStoredTXService
    from sto.models.implementation import BroadcastAccount, PreparedTransaction

    assert is_ethereum_network(network)

    check_good_private_key(ethereum_private_key)

    abi = get_abi(ethereum_abi_file)

    web3 = create_web3(ethereum_node_url)

    service = EthereumStoredTXService(
        network,
        dbsession,
        web3,
        ethereum_private_key,
        ethereum_gas_price,
        ethereum_gas_limit,
        BroadcastAccount,
        PreparedTransaction
    )
    note = "Deploying token contract for {}".format(contract_name)
    service.deploy_contract(
        contract_name=contract_name,
        abi=abi,
        note=note,
        constructor_args=contructor_args
    )


def get_kyc_deployed_tx(dbsession):
    from sto.models.implementation import PreparedTransaction
    # FIXME: fix this query
    # return dbsession.query(PreparedTransaction).filter(PreparedTransaction.contract_name == 'BasicKYC').first()
    txs = dbsession.query(PreparedTransaction).all()
    for tx in txs:
        if tx.contract_name == 'BasicKYC':
            return tx


def whitelist_kyc_address(
        dbsession,
        ethereum_private_key,
        ethereum_abi_file,
        ethereum_node_url,
        address,
        nonce
):
    from sto.ethereum.utils import get_kyc_deployed_tx
    from web3.middleware.signing import construct_sign_and_send_raw_middleware
    from eth_account import Account
    tx = get_kyc_deployed_tx(dbsession)
    if not tx:
        raise Exception(
            'BasicKyc contract is not deployed. '
            'invoke command kyc_deploy to deploy the smart contract'
        )

    check_good_private_key(ethereum_private_key)

    abi = get_abi(ethereum_abi_file)
    abi = abi['BasicKYC']['abi']

    w3 = create_web3(ethereum_node_url)

    contract = w3.eth.contract(address=tx.contract_address, abi=abi)
    w3.middleware_stack.add(construct_sign_and_send_raw_middleware(ethereum_private_key))
    account = Account.privateKeyToAccount(ethereum_private_key)
    tx_hash = contract.functions.whitelistUser(address, nonce).transact({'from': account.address})
    receipt = w3.eth.waitForTransactionReceipt(tx_hash)
    assert receipt['status'] == 1, "failed to whitelist address"