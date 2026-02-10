#!/bin/bash
python monitor.py &
python telegram_bot.py &
streamlit run Home.py --server.port 8080 --server.address 0.0.0.0
