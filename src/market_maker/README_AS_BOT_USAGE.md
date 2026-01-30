# List available NBA markets
python run_as_bot.py --list-markets --series KXNBAGAME -v

# Dry run on a market
python run_as_bot.py --ticker KXNBAGAME-26JAN21-CHI --dry-run -vv

# Live trading (requires confirmation)
python run_as_bot.py --ticker KXNBAGAME-26JAN21-CHI --live --gamma 0.05 --max-position 5