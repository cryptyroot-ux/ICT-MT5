#ifndef SMC_CONFIG_MQH
#define SMC_CONFIG_MQH

#define SMC_STRATEGY_NAME "SMC-H1M5-SweepFVG"
#define SMC_VERSION "1.0.0-research"
#define SMC_CONTEXT_TF PERIOD_H1
#define SMC_EXECUTION_TF PERIOD_M5
#define SMC_SWING_SPAN 2
#define SMC_MAX_LIQUIDITY_AGE_BARS 100
#define SMC_SWEEP_THRESHOLD_TICKS 2.0
#define SMC_MAX_RECLAIM_BARS 3
#define SMC_ATR_PERIOD 14
#define SMC_MIN_NET_RR 3.0
#define SMC_PENDING_EXPIRY_BARS 6
#define SMC_PENDING_EXPIRY_SECONDS 1800
#define SMC_MAX_SPREAD_PRICE 0.50
#define SMC_DEFAULT_RISK_PERCENT 0.50
#define SMC_MIN_RISK_PERCENT 0.25
#define SMC_MAX_RISK_PERCENT 0.50
#define SMC_DAILY_LOSS_PERCENT 2.0
#define SMC_MAX_CONSECUTIVE_LOSSES 2
#define SMC_DISPLACEMENT_ATR 1.0
#define SMC_DISPLACEMENT_BODY_RATIO 0.60
#define SMC_DISPLACEMENT_CLOSE_QUARTILE 0.25
#define SMC_EQUAL_LEVEL_TICKS 2.0

enum SMC_DIRECTION
  {
   SMC_DIRECTION_NONE=0,
   SMC_DIRECTION_BUY=1,
   SMC_DIRECTION_SELL=-1
  };

enum SMC_SETUP_STATE
  {
   SMC_WAIT_BIAS=0,
   SMC_WAIT_SWEEP=1,
   SMC_WAIT_DISPLACEMENT_CHOCH=2,
   SMC_WAIT_FVG=3,
   SMC_WAIT_RETRACE_OR_ORDER=4,
   SMC_ORDER_PENDING=5,
   SMC_IN_POSITION=6,
   SMC_COMPLETED=7,
   SMC_INVALIDATED=8,
   SMC_EXPIRED=9
  };

enum SMC_FVG_STATE
  {
   SMC_FVG_FRESH=0,
   SMC_FVG_PARTIALLY_MITIGATED=1,
   SMC_FVG_CONSUMED=2,
   SMC_FVG_INVALIDATED=3
  };

struct SMC_STRUCTURE_EVENT
  {
   SMC_DIRECTION direction;
   double        level;
   datetime      pivot_time;
   datetime      confirmation_time;
  };

struct SMC_SWEEP_EVENT
  {
   bool          valid;
   SMC_DIRECTION direction;
   double        level;
   double        extreme;
   datetime      pivot_time;
   datetime      excursion_time;
   datetime      confirmation_time;
   string        setup_id;
  };

struct SMC_DISPLACEMENT_EVENT
  {
   bool          valid;
   SMC_DIRECTION direction;
   datetime      displacement_bar_time;
   double        broken_level;
   double        atr_value;
   double        body_size;
   double        body_range_ratio;
   double        close_location;
   datetime      confirmation_time;
  };

struct SMC_FVG
  {
   bool          valid;
   SMC_DIRECTION direction;
   double        lower;
   double        upper;
   datetime      c1_time;
   datetime      displacement_time;
   datetime      creation_time;
   SMC_FVG_STATE state;
  };

struct SMC_SETUP
  {
   string           setup_id;
   SMC_SETUP_STATE  state;
   SMC_DIRECTION    direction;
   datetime         setup_start_time;
   datetime         last_transition_time;
   string           last_reason;
   double           h1_bos_level;
   datetime         h1_bos_time;
   datetime         h1_bos_confirmation;
   double           liquidity_level;
   double           sweep_extreme;
   datetime         sweep_time;
   datetime         sweep_confirmation;
   double           choch_level;
   datetime         displacement_bar_time;
   datetime         choch_confirmation;
   double           fvg_lower;
   double           fvg_upper;
   datetime         fvg_creation;
   double           entry_price;
   double           stop_loss;
   double           take_profit;
   datetime         target_liquidity_time;
   double           atr_value;
   double           volume;
   double           expected_risk;
   double           expected_net_rr;
   double           actual_fill_price;
   double           actual_slippage;
   double           position_volume;
   ulong            order_ticket;
   ulong            deal_id;
   ulong            position_id;
   datetime         order_reference_time;
   datetime         order_expiry;
  };

string SMCStateName(const SMC_SETUP_STATE state)
  {
   switch(state)
     {
      case SMC_WAIT_BIAS:                 return "WAIT_BIAS";
      case SMC_WAIT_SWEEP:                return "WAIT_SWEEP";
      case SMC_WAIT_DISPLACEMENT_CHOCH:   return "WAIT_DISPLACEMENT_CHOCH";
      case SMC_WAIT_FVG:                  return "WAIT_FVG";
      case SMC_WAIT_RETRACE_OR_ORDER:     return "WAIT_RETRACE_OR_ORDER";
      case SMC_ORDER_PENDING:             return "ORDER_PENDING";
      case SMC_IN_POSITION:               return "IN_POSITION";
      case SMC_COMPLETED:                 return "COMPLETED";
      case SMC_INVALIDATED:               return "INVALIDATED";
      case SMC_EXPIRED:                   return "EXPIRED";
     }
   return "UNKNOWN";
  }

string SMCDirectionName(const SMC_DIRECTION direction)
  {
   if(direction==SMC_DIRECTION_BUY) return "BUY";
   if(direction==SMC_DIRECTION_SELL) return "SELL";
   return "NONE";
  }

int SMCVolumeDigits(const double step)
  {
   for(int digits=0;digits<=8;digits++)
      if(MathAbs(NormalizeDouble(step,digits)-step)<1e-10)
         return digits;
   return 8;
  }

double SMCFloorToTick(const double price,const double tick_size)
  {
   if(tick_size<=0.0) return 0.0;
   return MathFloor(price/tick_size+1e-10)*tick_size;
  }

double SMCCeilToTick(const double price,const double tick_size)
  {
   if(tick_size<=0.0) return 0.0;
   return MathCeil(price/tick_size-1e-10)*tick_size;
  }

double SMCRoundToTick(const double price,const double tick_size)
  {
   if(tick_size<=0.0) return 0.0;
   return MathRound(price/tick_size)*tick_size;
  }

string SMCMakeSetupId(const string symbol,const SMC_DIRECTION direction,
                      const datetime excursion_time,const double level,
                      const double tick_size)
  {
   if(tick_size<=0.0) return "";
   const long level_ticks=(long)MathRound(level/tick_size);
   return StringFormat("%s-%s-%I64d-%I64d",symbol,SMCDirectionName(direction),
                       (long)excursion_time,level_ticks);
  }

bool SMCValidateConfiguration(const string symbol,const ENUM_TIMEFRAMES chart_tf,
                              const double risk_percent,const double daily_loss_percent,
                              const bool cost_model_verified,const double commission_per_lot,
                              const double slippage_per_lot,string &reason)
  {
   reason="";
   if(symbol=="")
     {
      reason="empty_symbol";
      return false;
     }
   if(chart_tf!=SMC_EXECUTION_TF)
     {
      reason="execution_chart_must_be_M5";
      return false;
     }
   if(risk_percent<SMC_MIN_RISK_PERCENT || risk_percent>SMC_MAX_RISK_PERCENT)
     {
      reason="risk_percent_outside_0.25_to_0.50";
      return false;
     }
   if(daily_loss_percent!=SMC_DAILY_LOSS_PERCENT)
     {
      reason="daily_loss_limit_is_frozen_at_2_percent";
      return false;
     }
   if(!cost_model_verified || commission_per_lot<0.0 || slippage_per_lot<0.0)
     {
      reason="verified_nonnegative_cost_model_required";
      return false;
     }
   if(!SymbolSelect(symbol,true))
     {
      reason="symbol_not_available_in_market_watch";
      return false;
     }

   double tick_size=0.0,tick_value=0.0,volume_min=0.0,volume_max=0.0,volume_step=0.0,point=0.0;
   if(!SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0 ||
      !SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE,tick_value) || tick_value<=0.0 ||
      !SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN,volume_min) || volume_min<=0.0 ||
      !SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX,volume_max) || volume_max<volume_min ||
      !SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP,volume_step) || volume_step<=0.0 ||
      !SymbolInfoDouble(symbol,SYMBOL_POINT,point) || point<=0.0)
     {
      reason="required_symbol_trade_properties_missing_or_invalid";
      return false;
     }
   return true;
  }

#endif
