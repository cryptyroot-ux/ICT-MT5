#ifndef SMC_RISK_MANAGER_MQH
#define SMC_RISK_MANAGER_MQH

#include <SMC_Config.mqh>

class CSMCRiskManager
  {
private:
   string   m_symbol;
   long     m_magic;
   double   m_risk_percent;
   double   m_daily_loss_percent;
   double   m_commission_round_turn_per_lot;
   double   m_slippage_round_turn_per_lot;
   double   m_equity_reference;
   int      m_day_key;
   string   m_day_state_key;
   bool     m_cost_model_verified;
   bool     m_state_reliable;

   int ServerDayKey(const datetime now) const
     {
      MqlDateTime parts;
      TimeToStruct(now,parts);
      return parts.year*10000+parts.mon*100+parts.day;
     }

   string BaselineKey(const int day_key) const
     {
      return StringFormat("SMC_EQ_%I64d_%I64d_%s_%d",
                          (long)AccountInfoInteger(ACCOUNT_LOGIN),m_magic,m_symbol,day_key);
     }

   string DayStateKey() const
     {
      return StringFormat("SMC_DAY_%I64d_%I64d_%s",
                          (long)AccountInfoInteger(ACCOUNT_LOGIN),m_magic,m_symbol);
     }

   bool LoadOrCreateBaseline(const datetime now)
     {
      m_day_key=ServerDayKey(now);
      const string key=BaselineKey(m_day_key);
      m_day_state_key=DayStateKey();
      if(GlobalVariableCheck(key))
        {
         m_equity_reference=GlobalVariableGet(key);
         if(GlobalVariableSet(m_day_state_key,(double)m_day_key)==0)
            return false;
         return (m_equity_reference>0.0 && MathIsValidNumber(m_equity_reference));
        }
      if(GlobalVariableCheck(m_day_state_key) &&
         (int)GlobalVariableGet(m_day_state_key)==m_day_key)
         return false; // Same-day reference disappeared; do not reset the cap on restart.
      m_equity_reference=AccountInfoDouble(ACCOUNT_EQUITY);
      if(m_equity_reference<=0.0 || !MathIsValidNumber(m_equity_reference))
         return false;
      if(GlobalVariableSet(key,m_equity_reference)==0 ||
         GlobalVariableSet(m_day_state_key,(double)m_day_key)==0)
         return false;
      GlobalVariablesFlush();
      return true;
     }

   bool TickValues(double &tick_size,double &tick_value_loss,double &tick_value_profit) const
     {
      tick_size=0.0;
      tick_value_loss=0.0;
      tick_value_profit=0.0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0)
         return false;
      SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_VALUE_LOSS,tick_value_loss);
      SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_VALUE_PROFIT,tick_value_profit);
      double generic_tick_value=0.0;
      SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_VALUE,generic_tick_value);
      if(tick_value_loss<=0.0) tick_value_loss=generic_tick_value;
      if(tick_value_profit<=0.0) tick_value_profit=generic_tick_value;
      return (tick_value_loss>0.0 && tick_value_profit>0.0);
     }

   bool CostPerLot(const double spread,double &cost) const
     {
      cost=0.0;
      if(spread<0.0 || !m_cost_model_verified)
         return false;
      double tick_size=0.0,tick_loss=0.0,tick_profit=0.0;
      if(!TickValues(tick_size,tick_loss,tick_profit))
         return false;
      cost=(spread/tick_size)*MathMax(tick_loss,tick_profit)+
           m_commission_round_turn_per_lot+m_slippage_round_turn_per_lot;
      return (cost>=0.0 && MathIsValidNumber(cost));
     }

public:
                     CSMCRiskManager()
     {
      m_symbol="";
      m_magic=0;
      m_risk_percent=SMC_DEFAULT_RISK_PERCENT;
      m_daily_loss_percent=SMC_DAILY_LOSS_PERCENT;
      m_commission_round_turn_per_lot=0.0;
      m_slippage_round_turn_per_lot=0.0;
      m_equity_reference=0.0;
      m_day_key=0;
      m_day_state_key="";
      m_cost_model_verified=false;
      m_state_reliable=false;
     }

   bool Init(const string symbol,const long magic,const double risk_percent,
             const double daily_loss_percent,const double commission_round_turn_per_lot,
             const double slippage_round_turn_per_lot,const bool cost_model_verified)
     {
      m_symbol=symbol;
      m_magic=magic;
      m_risk_percent=risk_percent;
      m_daily_loss_percent=daily_loss_percent;
      m_commission_round_turn_per_lot=commission_round_turn_per_lot;
      m_slippage_round_turn_per_lot=slippage_round_turn_per_lot;
      m_cost_model_verified=cost_model_verified;
      m_state_reliable=false;
      if(symbol=="" || magic<=0 || risk_percent<SMC_MIN_RISK_PERCENT ||
         risk_percent>SMC_MAX_RISK_PERCENT || daily_loss_percent!=SMC_DAILY_LOSS_PERCENT ||
         commission_round_turn_per_lot<0.0 || slippage_round_turn_per_lot<0.0 ||
         !cost_model_verified)
         return false;
      datetime now=TimeTradeServer();
      if(now<=0) now=TimeCurrent();
      m_state_reliable=LoadOrCreateBaseline(now);
      return m_state_reliable;
     }

   bool RefreshDay()
     {
      datetime now=TimeTradeServer();
      if(now<=0) now=TimeCurrent();
      const int today=ServerDayKey(now);
      if(today==m_day_key)
         return m_state_reliable;
      m_day_key=today;
      m_equity_reference=AccountInfoDouble(ACCOUNT_EQUITY);
      const string key=BaselineKey(m_day_key);
      m_state_reliable=(m_equity_reference>0.0 && MathIsValidNumber(m_equity_reference) &&
                        GlobalVariableSet(key,m_equity_reference)!=0 &&
                        GlobalVariableSet(m_day_state_key,(double)m_day_key)!=0);
      if(m_state_reliable)
         GlobalVariablesFlush();
      return m_state_reliable;
     }

   bool DailyLossReached() const
     {
      if(!m_state_reliable || m_equity_reference<=0.0)
         return true;
      const double equity=AccountInfoDouble(ACCOUNT_EQUITY);
      if(!MathIsValidNumber(equity))
         return true;
      return equity<=m_equity_reference*(1.0-m_daily_loss_percent/100.0);
     }

   bool CanOpenTrade(const int consecutive_losses)
     {
      if(!RefreshDay() || !m_state_reliable || !m_cost_model_verified)
         return false;
      if(consecutive_losses>=SMC_MAX_CONSECUTIVE_LOSSES || DailyLossReached())
         return false;
      return true;
     }

   double EquityReference() const { return m_equity_reference; }
   double RiskBudget() const { return m_equity_reference*m_risk_percent/100.0; }
   bool StateReliable() const { return m_state_reliable; }

   bool CalculateVolume(const SMC_DIRECTION direction,const double entry,const double stop,
                        const double spread,
                        double &volume,double &final_loss) const
     {
      volume=0.0;
      final_loss=0.0;
      if(!m_state_reliable || !m_cost_model_verified || direction==SMC_DIRECTION_NONE ||
         entry<=0.0 || stop<=0.0 || RiskBudget()<=0.0)
         return false;

      double tick_size=0.0,tick_loss=0.0,tick_profit=0.0;
      if(!TickValues(tick_size,tick_loss,tick_profit))
         return false;
      const ENUM_ORDER_TYPE order_type=(direction==SMC_DIRECTION_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double profit_one_lot=0.0;
      if(!OrderCalcProfit(order_type,m_symbol,1.0,entry,stop,profit_one_lot) || profit_one_lot>=0.0)
         return false;
      const double calc_loss=-profit_one_lot;
      const double tick_estimate=(MathAbs(entry-stop)/tick_size)*tick_loss;
      if(tick_estimate<=0.0 || MathAbs(tick_estimate-calc_loss)/calc_loss>0.10)
         return false;
      double cost_per_lot=0.0;
      if(!CostPerLot(spread,cost_per_lot))
         return false;

      double minimum=0.0,maximum=0.0,step=0.0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_MIN,minimum) ||
         !SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_MAX,maximum) ||
         !SymbolInfoDouble(m_symbol,SYMBOL_VOLUME_STEP,step) ||
         minimum<=0.0 || maximum<minimum || step<=0.0)
         return false;

      const double raw=RiskBudget()/(calc_loss+cost_per_lot);
      if(raw<minimum)
         return false;
      double lots=MathFloor(raw/step+1e-10)*step;
      if(lots>maximum)
         lots=MathFloor(maximum/step+1e-10)*step;
      lots=NormalizeDouble(lots,SMCVolumeDigits(step));
      if(lots<minimum || lots<=0.0)
         return false;

      double final_profit=0.0;
      if(!OrderCalcProfit(order_type,m_symbol,lots,entry,stop,final_profit) || final_profit>=0.0)
         return false;
      const double monetary_loss=-final_profit+cost_per_lot*lots;
      if(monetary_loss>RiskBudget()+0.01)
         return false;
      volume=lots;
      final_loss=monetary_loss;
      return true;
     }

   bool CalculateActualLoss(const SMC_DIRECTION direction,const double entry,const double stop,
                            const double volume,const double spread,double &loss) const
     {
      loss=0.0;
      if(direction==SMC_DIRECTION_NONE || entry<=0.0 || stop<=0.0 || volume<=0.0)
         return false;
      double tick_size=0.0,tick_loss=0.0,tick_profit=0.0;
      if(!TickValues(tick_size,tick_loss,tick_profit))
         return false;
      const ENUM_ORDER_TYPE order_type=(direction==SMC_DIRECTION_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double profit=0.0;
      if(!OrderCalcProfit(order_type,m_symbol,volume,entry,stop,profit) || profit>=0.0)
         return false;
      const double tick_estimate=(MathAbs(entry-stop)/tick_size)*tick_loss*volume;
      const double calc_loss=-profit;
      if(tick_estimate<=0.0 || MathAbs(tick_estimate-calc_loss)/calc_loss>0.10)
         return false;
      double cost_per_lot=0.0;
      if(!CostPerLot(spread,cost_per_lot))
         return false;
      loss=calc_loss+cost_per_lot*volume;
      return true;
     }

   bool CalculateNetRewardRisk(const SMC_DIRECTION direction,const double entry,
                               const double stop,const double target,const double volume,
                               const double spread,double &net_rr) const
     {
      net_rr=0.0;
      if(!m_state_reliable || !m_cost_model_verified || direction==SMC_DIRECTION_NONE ||
         entry<=0.0 || stop<=0.0 || target<=0.0 || volume<=0.0 || spread<0.0)
         return false;
      double tick_size=0.0,tick_loss=0.0,tick_profit=0.0;
      if(!TickValues(tick_size,tick_loss,tick_profit))
         return false;
      const ENUM_ORDER_TYPE order_type=(direction==SMC_DIRECTION_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double stop_profit=0.0,target_profit=0.0;
      if(!OrderCalcProfit(order_type,m_symbol,volume,entry,stop,stop_profit) || stop_profit>=0.0 ||
         !OrderCalcProfit(order_type,m_symbol,volume,entry,target,target_profit) || target_profit<=0.0)
         return false;
      const double spread_cost=(spread/tick_size)*MathMax(tick_loss,tick_profit)*volume;
      const double other_cost=(m_commission_round_turn_per_lot+m_slippage_round_turn_per_lot)*volume;
      const double total_cost=spread_cost+other_cost;
      const double net_reward=target_profit-total_cost;
      const double net_risk=-stop_profit+total_cost;
      if(net_reward<=0.0 || net_risk<=0.0)
         return false;
      net_rr=net_reward/net_risk;
      return MathIsValidNumber(net_rr);
     }
  };

#endif
