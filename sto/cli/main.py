"""Define command line interface and subcommands. """
import logging
import os
import sys

import colorama
import configobj
import pkg_resources

import click
import coloredlogs
from sto.db import setup_database


class UnknownConfiguredNetwork(Exception):
    pass


class BoardCommmadConfiguration:
    """All top level option switcs either read command line or INI

    See ``main.cli()`` arguments for content.
    """

    def __init__(self, **kwargs):
        # See cli() for descriptions of variables
        self.__dict__.update(kwargs)


def create_command_line_logger(log_level):
    """Create a fancy output.

    See: https://coloredlogs.readthedocs.io/en/latest/readme.html#installation
    """
    fmt = "%(message)s"
    logger = logging.getLogger()
    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)
    return logger


def is_ethereum_network(network: str):
    return network in ("ethereum", "kovan", "ropsten")



INTRO_TEXT = """{}TokenMarket{} security token management tool.

    {}Manage tokenised equity for things like issuing out new, distributing and revoking shares.{}
    
    For full documentation see https://docs.tokenmarket.net/
""".format(colorama.Fore.LIGHTGREEN_EX, colorama.Fore.RESET, colorama.Fore.BLUE, colorama.Fore.RESET)



@click.group(help=INTRO_TEXT)
@click.option('--config-file', required=False, default=None, help="INI file where to read options from", type=click.Path())
@click.option('--database-file', required=False, default="transactions.sqlite", help="SQLite file that persists transaction broadcast status", type=click.Path())
@click.option('--network', required=False, default="ethereum", help="Network name. Either 'ethereum' or 'kovan' are supported for now.")
@click.option('--ethereum-node-url', required=False, default="http://localhost:8545", help="Parity or Geth JSON-RPC to connect for Ethereum network access")
@click.option('--ethereum-abi-file', required=False, help='Solidity compiler output JSON to override default smart contracts')
@click.option('--ethereum-gas-price', required=False, help='How many GWei we pay for gas')
@click.option('--ethereum-gas-limit', required=False, help='What is the transaction gas limit for broadcasts', type=int)
@click.option('--ethereum-private-key', required=False, help='Private key for the broadcasting account')
@click.option('--etherscan-api-key', required=False, help='EtherScan API key used for the contract source code verification')
@click.option('--log-level', default="INFO", help="Python logging level to tune the verbosity of the command")
@click.option('--auto-restart-nonce', default=True, help="Automatically restart nonce for the deployment account if starting with a fresh database", type=bool)
@click.pass_context
def cli(ctx, config_file, **kwargs):

    # Fill in arguments from the configuration file
    if config_file:
        if not os.path.exists(config_file):

            sys.exit("Config file does not exist {}".format(config_file))

        config = configobj.ConfigObj(config_file, raise_errors=True)

        # TODO: Bug here - could not figure out how to pull out from click if an option is set on a command line or are we using default.
        # Thus you cannot override config file variables by giving a default value from command line
        for opt in ctx.command.params:  # type: click.core.Options:

            dashed_name = opt.name.replace("_", "-")
            value = kwargs.get(opt.name)
            if value == opt.default:
                config_file_value = config.get(dashed_name)
                if config_file_value:
                    kwargs[opt.name] = config_file_value  # TODO: opt.process_value

    log_level = kwargs["log_level"]

    config = BoardCommmadConfiguration(**kwargs)
    logger = config.logger = create_command_line_logger(log_level.upper())

    # Mute SQLAlchemy logger who is quite a verbose friend otherwise
    sa_logger = logging.getLogger("sqlalchemy")
    sa_logger.setLevel(logging.WARN)

    # Print out the info
    dbfile = os.path.abspath(config.database_file)
    version = pkg_resources.require("sto")[0].version
    copyright = "Copyright TokenMarket Ltd. 2018 - 2019"
    logger.info("STO tool, version %s%s%s - %s", colorama.Fore.LIGHTCYAN_EX, version, colorama.Fore.RESET, copyright)
    logger.info("Using database %s%s%s", colorama.Fore.LIGHTCYAN_EX, dbfile, colorama.Fore.RESET)

    config.dbsession, new_db = setup_database(logger, dbfile)
    if new_db:
        if config.auto_restart_nonce and config.ethereum_private_key:
            logger.info("Automatically fetching the initial nonce for the deployment account from blockchain")
            from sto.ethereum.nonce import restart_nonce
            restart_nonce(logger,
                          config.dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price)

    ctx.obj = config


@cli.command()
@click.option('--symbol', required=True)
@click.option('--name', required=True)
@click.option('--amount', required=True, type=int)
@click.option('--transfer-restriction', required=False, default="unrestricted")
@click.pass_obj
def issue(config: BoardCommmadConfiguration, symbol, name, amount, transfer_restriction):
    """Issue out a new security token.

    * Creates a new share series

    * Allocates all new shares to the management account

    * Sets the share transfer restriction mode
    """

    logger = config.logger

    assert is_ethereum_network(config.network) # Nothing else implemented yet

    from sto.ethereum.issuance import deploy_token_contracts
    from sto.ethereum.txservice import EthereumStoredTXService

    dbsession = config.dbsession

    txs = deploy_token_contracts(logger,
                          dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_abi_file=config.ethereum_abi_file,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price,
                          name=name,
                          symbol=symbol,
                          amount=amount,
                          transfer_restriction=transfer_restriction)

    EthereumStoredTXService.print_transactions(txs)

    # Write database
    dbsession.commit()

    logger.info("Run %ssto tx-broadcast%s to write this to blockchain", colorama.Fore.LIGHTCYAN_EX, colorama.Fore.RESET)


@cli.command(name="issue-logs")
@click.pass_obj
def past_issuances(config: BoardCommmadConfiguration):
    """Print out transactions of for tokens issued in the past."""

    logger = config.logger

    from sto.ethereum.issuance import past_issuances

    dbsession = config.dbsession

    txs = list(past_issuances(config, dbsession))

    if txs:
        from sto.ethereum.txservice import EthereumStoredTXService
        EthereumStoredTXService.print_transactions(txs)
        logger.info("See column %sto%s for token contract addresses", colorama.Fore.LIGHTCYAN_EX, colorama.Fore.RESET)
    else:
        logger.info("No issuances")



@cli.command(name="token-status")
@click.option('--address', required=True, help="Token contract addrss")
@click.pass_obj
def status(config: BoardCommmadConfiguration, address):
    """Print token contract status."""

    logger = config.logger

    assert is_ethereum_network(config.network) # Nothing else implemented yet

    from sto.ethereum.issuance import contract_status

    dbsession = config.dbsession

    contract_status(logger,
      dbsession,
      config.network,
      ethereum_node_url=config.ethereum_node_url,
      ethereum_abi_file=config.ethereum_abi_file,
      ethereum_private_key=config.ethereum_private_key,
      ethereum_gas_limit=config.ethereum_gas_limit,
      ethereum_gas_price=config.ethereum_gas_price,
      token_contract=address)


@cli.command(name="distribute-multiple")
@click.option('--csv-input', required=True, help="CSV file for entities receiving tokens")
@click.option('--address', required=True, help="Token contract address")
@click.pass_obj
def distribute_multiple(config: BoardCommmadConfiguration, csv_input, address):
    """Distribute shares to multiple shareholders whose address info is read from a file."""

    logger = config.logger

    assert is_ethereum_network(config.network) # Nothing else implemented yet
    dbsession = config.dbsession

    from sto.distribution import read_csv
    from sto.ethereum.distribution import distribute_tokens

    dists = read_csv(logger, csv_input)
    if not dists:
        sys.exit("Empty CSV file")

    new_txs, old_txs = distribute_tokens(
        logger,
        dbsession,
        config.network,
        ethereum_node_url=config.ethereum_node_url,
        ethereum_abi_file=config.ethereum_abi_file,
        ethereum_private_key=config.ethereum_private_key,
        ethereum_gas_limit=config.ethereum_gas_limit,
        ethereum_gas_price=config.ethereum_gas_price,
        token_address=address,
        dists=dists,
    )

    logger.info("Distribution created %d new transactions and there was already %d old transactions in the database", new_txs, old_txs)

    # Write database
    dbsession.commit()

    logger.info("Run %ssto tx-broadcast%s to send out distribured shares to the world", colorama.Fore.LIGHTCYAN_EX, colorama.Fore.RESET)


@cli.command(name="distribute-single")
@click.option('--token-address', required=True, help="Token contract address")
@click.option('--to-address', required=True, help="Receiver")
@click.option('--external-id', required=True, help="External id string for this transaction - no duplicates allowed")
@click.option('--email', required=True, help="Receiver email (for audit log only)")
@click.option('--name', required=True, help="Receiver name (for audit log only)")
@click.option('--amount', required=True, help="Amount of tokens as a decimal number")
@click.pass_obj
def distribute_single(config: BoardCommmadConfiguration, token_address, to_address, external_id, email, name, amount):
    """Send out tokens to one individual shareholder."""

    logger = config.logger

    assert is_ethereum_network(config.network) # Nothing else implemented yet
    dbsession = config.dbsession

    from sto.ethereum.distribution import distribute_single

    result = distribute_single(
        logger,
        dbsession,
        config.network,
        ethereum_node_url=config.ethereum_node_url,
        ethereum_abi_file=config.ethereum_abi_file,
        ethereum_private_key=config.ethereum_private_key,
        ethereum_gas_limit=config.ethereum_gas_limit,
        ethereum_gas_price=config.ethereum_gas_price,
        token_address=token_address,
        to_address=to_address,
        ext_id=external_id,
        email=email,
        name=name,
        amount=amount
    )

    if result:
        # Write database
        dbsession.commit()
        logger.info("Run %ssto tx-broadcast%s to send out distribured shares to the world", colorama.Fore.LIGHTCYAN_EX, colorama.Fore.RESET)


@cli.command()
@click.pass_obj
def diagnose(config: BoardCommmadConfiguration):
    """Check your node and account status.

    This command will print out if you are properly connected to Ethereum network and your management account has enough Ether balance.
    """

    # Run Ethereum diagnostics
    if is_ethereum_network(config.network):
        from sto.ethereum.diagnostics import diagnose

        private_key = config.ethereum_private_key
        exception = diagnose(config.logger, config.ethereum_node_url, private_key)
        if exception:
            config.logger.error("{}We identified an issue with your configuration. Please fix the issue above to use this command yet.{}".format(colorama.Fore.RED, colorama.Fore.RESET))
        else:
            config.logger.info("{}Ready for action.{}".format(colorama.Fore.LIGHTGREEN_EX, colorama.Fore.RESET))
    else:
        raise UnknownConfiguredNetwork()


@cli.command(name="ethereum-create-account")
@click.pass_obj
def create_ethereum_account(config: BoardCommmadConfiguration):
    """Creates a new Ethereum account."""

    config.logger.info("Creating new Ethereum account.")
    from sto.ethereum.account import create_account_console
    create_account_console(config.logger, config.network)


@cli.command(name="tx-broadcast")
@click.pass_obj
def broadcast(config: BoardCommmadConfiguration):
    """Broadcast waiting transactions.

    Send all management account transactions to Ethereum network.
    After a while, transactions are picked up by miners and included in the blockchain.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.broadcast import broadcast

    dbsession = config.dbsession

    txs = broadcast(logger,
                          dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price)

    if txs:
        from sto.ethereum.txservice import EthereumStoredTXService
        EthereumStoredTXService.print_transactions(txs)
        logger.info("Run %ssto tx-update%s to monitor your transaction propagation status", colorama.Fore.LIGHTCYAN_EX, colorama.Fore.RESET)

    # Write database
    dbsession.commit()


@cli.command(name="tx-update")
@click.pass_obj
def update(config: BoardCommmadConfiguration):
    """Update transaction status.

    Connects to Ethereum network, queries the status of our broadcasted transactions.
    Then print outs the still currently pending transactions or freshly mined transactions.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.status import update_status

    dbsession = config.dbsession

    txs = update_status(logger,
                          dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price)

    if txs:
        from sto.ethereum.txservice import EthereumStoredTXService
        EthereumStoredTXService.print_transactions(txs)

    # Write database
    dbsession.commit()



@cli.command(name="tx-verify")
@click.pass_obj
def verify(config: BoardCommmadConfiguration):
    """Verify source code of contract deployment transactions on EtherScan.

    Users EtherScan API to verify all deployed contracts from the management account.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.issuance import verify_source_code

    dbsession = config.dbsession

    txs = verify_source_code(logger,
                          dbsession,
                          config.network,
                          config.etherscan_api_key)

    if txs:
        from sto.ethereum.txservice import EthereumStoredTXService
        EthereumStoredTXService.print_transactions(txs)

    # Write database
    dbsession.commit()


@cli.command(name="tx-last")
@click.option('--limit', required=False, help="How many transactions to print", default=5)
@click.pass_obj
def last(config: BoardCommmadConfiguration, limit):
    """Print latest transactions from database.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.last import get_last_transactions

    dbsession = config.dbsession

    txs = get_last_transactions(logger,
                          dbsession,
                          config.network,
                          limit=limit,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price)

    if txs:
        from sto.ethereum.txservice import EthereumStoredTXService
        EthereumStoredTXService.print_transactions(txs)


@cli.command(name="tx-restart-nonce")
@click.pass_obj
def restart_nonce(config: BoardCommmadConfiguration):
    """Resets the broadcasting account nonce."""

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.nonce import restart_nonce

    dbsession = config.dbsession

    restart_nonce(logger,
          dbsession,
          config.network,
          ethereum_node_url=config.ethereum_node_url,
          ethereum_private_key=config.ethereum_private_key,
          ethereum_gas_limit=config.ethereum_gas_limit,
          ethereum_gas_price=config.ethereum_gas_price)

@cli.command(name="tx-next-nonce")
@click.pass_obj
def next_nonce(config: BoardCommmadConfiguration):
    """Print next nonce to be consumed."""

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.nonce import next_nonce

    dbsession = config.dbsession

    txs = next_nonce(logger,
                          dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_private_key=config.ethereum_private_key,
                          ethereum_gas_limit=config.ethereum_gas_limit,
                          ethereum_gas_price=config.ethereum_gas_price)


@cli.command(name="token-scan")
@click.option('--start-block', required=False, help="The first block where we start (re)scan", default=None)
@click.option('--end-block', required=False, help="Until which block we scan, also can be 'latest'", default=None)
@click.option('--token-address', required=True, help="Token contract address", default=None)
@click.pass_obj
def token_scan(config: BoardCommmadConfiguration, token_address, start_block, end_block):
    """Update token holder balances from a blockchain to a local database.

    Reads the Ethereum blockchain for a certain token and builds a local database of token holders and transfers.

    If start block and end block information are omitted, continue the scan where we were left last time.
    Scan operations may take a while.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.ethereum.tokenscan import token_scan

    dbsession = config.dbsession

    updated_addresses = token_scan(logger,
                          dbsession,
                          config.network,
                          ethereum_node_url=config.ethereum_node_url,
                          ethereum_abi_file=config.ethereum_abi_file,
                          token_address=token_address,
                          start_block=start_block,
                          end_block=end_block)

    logger.info("Updated %d token holder balances", len(updated_addresses))


@cli.command(name="cap-table")
@click.option('--identity-file', required=False, help="CSV file containing address real world identities", default=None, type=click.Path())
@click.option('--token-address', required=True, help="Token contract address", default=None)
@click.option('--order-by', required=False, help="How cap table is sorted", default="balance", type=click.Choice(["balance", "name", "updated", "address"]))
@click.option('--order-direction', required=False, help="Sort direction", default="desc", type=click.Choice(["asc", "desc"]))
@click.option('--include-empty', required=False, help="Sort direction", default=False, type=bool)
@click.option('--max-entries', required=False, help="Print only first N entries", default=5000, type=int)
@click.option('--accuracy', required=False, help="How many decimals include in balance output", default=2, type=int)
@click.pass_obj
def cap_table(config: BoardCommmadConfiguration, token_address, identity_file, order_by, order_direction, include_empty, max_entries, accuracy):
    """Print out token holder cap table.

    The token holder data must have been scanned earlier using token-scan command.

    You can supply optional CSV file that contains Ethereum address mappings to individual token holder names.
    """

    assert is_ethereum_network(config.network)

    logger = config.logger

    from sto.generic.captable import generate_cap_table, print_cap_table
    from sto.identityprovider import read_csv, NullIdentityProvider, CSVIdentityProvider
    from sto.models.implementation import TokenScanStatus, TokenHolderAccount

    dbsession = config.dbsession

    if identity_file:
        entries = read_csv(logger, identity_file)
        provider = CSVIdentityProvider(entries)
    else:
        provider = NullIdentityProvider()

    cap_table = generate_cap_table(logger,
                          dbsession,
                          token_address=token_address,
                          identity_provider=provider,
                          order_by=order_by,
                          order_direction=order_direction,
                          include_empty=include_empty,
                          TokenScanStatus=TokenScanStatus,
                          TokenHolderAccount=TokenHolderAccount)

    print_cap_table(cap_table, max_entries, accuracy)


@cli.command(name="reference")
@click.pass_obj
def reference(config: BoardCommmadConfiguration):
    """Print out the command line reference for the documentation."""

    from sto.generic.reference import generate_reference
    generate_reference(cli)


def main():
    # https://github.com/pallets/click/issues/204#issuecomment-270012917
    cli.main(max_content_width=200, terminal_width=200)
