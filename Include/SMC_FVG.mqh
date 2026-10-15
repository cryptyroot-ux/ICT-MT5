#ifndef SMC_FVG_MQH
#define SMC_FVG_MQH

#include <SMC_Config.mqh>

class CSMCFairValueGap
  {
private:
   string m_symbol;

public:
                     CSMCFairValueGap() { m_symbol=""; }

   bool Init(const string symbol)
     {
      m_symbol=symbol;
      return (symbol!="");
     }

   bool FindAssociated(const SMC_SWEEP_EVENT &sweep,
                       const SMC_DISPLACEMENT_EVENT &displacement,
                       SMC_FVG &output) const
     {
      ZeroMemory(output);
      output.valid=false;
      output.direction=SMC_DIRECTION_NONE;
      output.state=SMC_FVG_FRESH;
      if(!sweep.valid || !displacement.valid || sweep.direction!=displacement.direction ||
         displacement.confirmation_time<=sweep.confirmation_time)
         return false;

      double tick_size=0.0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0)
         return false;
      const int available=Bars(m_symbol,SMC_EXECUTION_TF);
      const int requested=MathMin(available,SMC_MAX_LIQUIDITY_AGE_BARS+SMC_ATR_PERIOD+SMC_SWING_SPAN+8);
      if(requested<5)
         return false;
      MqlRates rates[];
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_EXECUTION_TF,0,requested,rates);
      if(copied<5)
         return false;
      ArrayResize(rates,copied);
      const int closed_end=copied-2;

      const int timeframe_seconds=PeriodSeconds(SMC_EXECUTION_TF);
      if(timeframe_seconds<=0)
         return false;
      for(int middle=1;middle+1<=closed_end;middle++)
        {
         if(rates[middle].time!=displacement.displacement_bar_time)
            continue;
         const MqlRates c1=rates[middle-1];
         const MqlRates c2=rates[middle];
         const MqlRates c3=rates[middle+1];
         if(c1.time<sweep.confirmation_time || c2.time<sweep.confirmation_time ||
            c3.time<displacement.confirmation_time)
            return false;

         double lower=0.0,upper=0.0;
         if(sweep.direction==SMC_DIRECTION_BUY)
           {
            lower=c1.high;
            upper=c3.low;
           }
         else
           {
            lower=c3.high;
            upper=c1.low;
           }
         if(upper<=lower || upper-lower<tick_size)
            return false;

         output.valid=true;
         output.direction=sweep.direction;
         output.lower=lower;
         output.upper=upper;
         output.c1_time=c1.time;
         output.displacement_time=c2.time;
         output.creation_time=c3.time+timeframe_seconds;
         output.state=SMC_FVG_FRESH;
         return true;
        }
      return false;
     }

   SMC_FVG_STATE StatusAfterCreation(const SMC_FVG &gap) const
     {
      if(!gap.valid || gap.upper<=gap.lower)
         return SMC_FVG_INVALIDATED;
      const int available=Bars(m_symbol,SMC_EXECUTION_TF);
      const int requested=MathMin(available,SMC_MAX_LIQUIDITY_AGE_BARS+SMC_SWING_SPAN+8);
      if(requested<3)
         return SMC_FVG_INVALIDATED;
      MqlRates rates[];
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_EXECUTION_TF,0,requested,rates);
      if(copied<3)
         return SMC_FVG_INVALIDATED;
      ArrayResize(rates,copied);
      const int closed_end=copied-2;
      SMC_FVG_STATE state=SMC_FVG_FRESH;

      for(int index=0;index<=closed_end;index++)
        {
         if(rates[index].time<gap.creation_time)
            continue;
         if(gap.direction==SMC_DIRECTION_BUY)
           {
            if(rates[index].close<gap.lower)
               return SMC_FVG_INVALIDATED;
            if(rates[index].low<=gap.lower)
               state=SMC_FVG_CONSUMED;
            else if(rates[index].low<=gap.upper && state==SMC_FVG_FRESH)
               state=SMC_FVG_PARTIALLY_MITIGATED;
           }
         else
           {
            if(rates[index].close>gap.upper)
               return SMC_FVG_INVALIDATED;
            if(rates[index].high>=gap.upper)
               state=SMC_FVG_CONSUMED;
            else if(rates[index].high>=gap.lower && state==SMC_FVG_FRESH)
               state=SMC_FVG_PARTIALLY_MITIGATED;
           }
        }
      return state;
     }
  };

#endif
