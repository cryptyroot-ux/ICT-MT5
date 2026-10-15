//+------------------------------------------------------------------+
//| SMC_SymbolSpec.mq5                                              |
//| Dumps the real broker symbol properties SMC-H1M5-SweepFVG       |
//| depends on. Run on the EXACT account/symbol you intend to trade  |
//| and paste the output into docs/BROKER_CONFIGURATION.md.          |
//| This is a measurement tool, not a trading EA.                    |
//+------------------------------------------------------------------+
#property version   "1.00"
#property strict

input string InpSymbol = "XAUUSD";

string Fmt(const double value, const int digits = 8) { return DoubleToString(value, digits); }

void DumpDouble(const string label, const ENUM_SYMBOL_INFO_DOUBLE prop, const int digits = 8)
  {
   double value = 0.0;
   if(!SymbolInfoDouble(InpSymbol, prop, value))
      Print(label, " = <unavailable>");
   else
      Print(label, " = ", Fmt(value, digits));
  }

void DumpInteger(const string label, const ENUM_SYMBOL_INFO_INTEGER prop)
  {
   long value = 0;
   if(!SymbolInfoInteger(InpSymbol, prop, value))
      Print(label, " = <unavailable>");
   else
      Print(label, " = ", (long)value);
  }

void DumpStringProp(const string label, const ENUM_SYMBOL_INFO_STRING prop)
  {
   string value = "";
   if(!SymbolInfoString(InpSymbol, prop, value))
      Print(label, " = <unavailable>");
   else
      Print(label, " = ", value);
  }

int OnInit()
  {
   if(!SymbolSelect(InpSymbol, true))
     {
      Print("SymbolSelect failed for ", InpSymbol);
      return INIT_FAILED;
     }

   Print("=== SMC-H1M5-SweepFVG symbol specification ===");
   Print("collected_at_server_time = ", TimeToString(TimeTradeServer(), TIME_DATE | TIME_SECONDS));
   Print("terminal_build = ", (string)TerminalInfoInteger(TERMINAL_BUILD));
   Print("account_login = ", (long)AccountInfoInteger(ACCOUNT_LOGIN));
   Print("account_server = ", AccountInfoString(ACCOUNT_SERVER));
   Print("account_trade_mode = ", (int)AccountInfoInteger(ACCOUNT_TRADE_MODE));
   Print("account_currency = ", AccountInfoString(ACCOUNT_CURRENCY));
   Print("account_equity = ", Fmt(AccountInfoDouble(ACCOUNT_EQUITY), 2));
   Print("account_profit_mode = ", (int)AccountInfoInteger(ACCOUNT_MARGIN_MODE) == 0 ? "netting" : "hedging");
   Print("symbol = ", InpSymbol);

   DumpStringProp("SYMBOL_DESCRIPTION", SYMBOL_DESCRIPTION);
   DumpStringProp("SYMBOL_CURRENCY_BASE", SYMBOL_CURRENCY_BASE);
   DumpStringProp("SYMBOL_CURRENCY_PROFIT", SYMBOL_CURRENCY_PROFIT);
   DumpStringProp("SYMBOL_CURRENCY_MARGIN", SYMBOL_CURRENCY_MARGIN);

   DumpInteger("SYMBOL_DIGITS", SYMBOL_DIGITS);
   DumpDouble("SYMBOL_POINT", SYMBOL_POINT, 10);
   DumpDouble("SYMBOL_TRADE_TICK_SIZE", SYMBOL_TRADE_TICK_SIZE, 10);
   DumpDouble("SYMBOL_TRADE_TICK_VALUE", SYMBOL_TRADE_TICK_VALUE, 8);
   DumpDouble("SYMBOL_TRADE_TICK_VALUE_PROFIT", SYMBOL_TRADE_TICK_VALUE_PROFIT, 8);
   DumpDouble("SYMBOL_TRADE_TICK_VALUE_LOSS", SYMBOL_TRADE_TICK_VALUE_LOSS, 8);
   DumpDouble("SYMBOL_TRADE_CONTRACT_SIZE", SYMBOL_TRADE_CONTRACT_SIZE, 4);
   DumpDouble("SYMBOL_VOLUME_MIN", SYMBOL_VOLUME_MIN, 4);
   DumpDouble("SYMBOL_VOLUME_MAX", SYMBOL_VOLUME_MAX, 4);
   DumpDouble("SYMBOL_VOLUME_STEP", SYMBOL_VOLUME_STEP, 4);
   DumpDouble("SYMBOL_TRADE_STOPS_LEVEL", SYMBOL_TRADE_STOPS_LEVEL, 2);
   DumpDouble("SYMBOL_TRADE_FREEZE_LEVEL", SYMBOL_TRADE_FREEZE_LEVEL, 2);
   DumpInteger("SYMBOL_FILLING_MODE", SYMBOL_FILLING_MODE);
   DumpInteger("SYMBOL_TRADE_EXEMODE", SYMBOL_TRADE_EXEMODE);
   DumpInteger("SYMBOL_TRADE_MODE", SYMBOL_TRADE_MODE);
   DumpDouble("SYMBOL_SWAP_LONG", SYMBOL_SWAP_LONG, 4);
   DumpDouble("SYMBOL_SWAP_SHORT", SYMBOL_SWAP_SHORT, 4);

   MqlTick tick;
   if(SymbolInfoTick(InpSymbol, tick))
      Print("live_bid = ", Fmt(tick.bid, (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS)),
            " live_ask = ", Fmt(tick.ask, (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS)),
            " live_spread = ", Fmt(tick.ask - tick.bid, 8));
   else
      Print("live tick unavailable");

   // Verify OrderCalcProfit agrees with the tick properties for a 1.00 price-unit stop.
   double tick_size = 0.0, tick_loss = 0.0;
   if(SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE, tick_size) && tick_size > 0.0 &&
      SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_VALUE_LOSS, tick_loss) && tick_loss > 0.0 &&
      tick.bid > 0.0)
     {
      const double stop = tick.bid - 1.0;
      double calc = 0.0;
      if(OrderCalcProfit(ORDER_TYPE_BUY, InpSymbol, 1.0, tick.bid, stop, calc))
        {
         const double tick_estimate = (1.0 / tick_size) * tick_loss;
         Print("loss_per_lot_1_price_unit_OrderCalcProfit = ", Fmt(-calc, 8));
         Print("loss_per_lot_1_price_unit_tick_properties  = ", Fmt(tick_estimate, 8));
         Print("relative_difference = ", Fmt(MathAbs(-calc - tick_estimate) / MathAbs(-calc), 6));
        }
      else
         Print("OrderCalcProfit failed with error ", GetLastError());
     }

   Print("=== end of specification ===");
   return INIT_SUCCEEDED;
  }

void OnTick() {}
