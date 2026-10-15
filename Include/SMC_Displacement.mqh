#ifndef SMC_DISPLACEMENT_MQH
#define SMC_DISPLACEMENT_MQH

#include <SMC_Config.mqh>

class CSMCDisplacement
  {
private:
   string m_symbol;

   bool IsPivotHigh(const MqlRates &rates[],const int index) const
     {
      for(int offset=1;offset<=SMC_SWING_SPAN;offset++)
         if(rates[index-offset].high>=rates[index].high || rates[index+offset].high>=rates[index].high)
            return false;
      return true;
     }

   bool IsPivotLow(const MqlRates &rates[],const int index) const
     {
      for(int offset=1;offset<=SMC_SWING_SPAN;offset++)
         if(rates[index-offset].low<=rates[index].low || rates[index+offset].low<=rates[index].low)
            return false;
      return true;
     }

   double ATRAt(const MqlRates &rates[],const int index,const int period) const
     {
      if(period<1 || index-period+1<1)
         return 0.0;
      double sum=0.0;
      for(int current=index-period+1;current<=index;current++)
        {
         const double previous_close=rates[current-1].close;
         const double true_range=MathMax(rates[current].high-rates[current].low,
                                  MathMax(MathAbs(rates[current].high-previous_close),
                                          MathAbs(rates[current].low-previous_close)));
         sum+=true_range;
        }
      return sum/(double)period;
     }

   bool IsDisplacement(const MqlRates &bar,const double atr,const SMC_DIRECTION direction,
                       double &body,double &body_ratio,double &close_location) const
     {
      body=MathAbs(bar.close-bar.open);
      const double range=bar.high-bar.low;
      body_ratio=0.0;
      close_location=0.0;
      if(atr<=0.0 || range<=0.0 || body<SMC_DISPLACEMENT_ATR*atr)
         return false;
      body_ratio=body/range;
      close_location=(bar.close-bar.low)/range;
      if(body_ratio<SMC_DISPLACEMENT_BODY_RATIO)
         return false;
      if(direction==SMC_DIRECTION_BUY)
         return (bar.close>bar.open && close_location>=1.0-SMC_DISPLACEMENT_CLOSE_QUARTILE);
      return (direction==SMC_DIRECTION_SELL && bar.close<bar.open &&
              close_location<=SMC_DISPLACEMENT_CLOSE_QUARTILE);
     }

public:
                     CSMCDisplacement() { m_symbol=""; }

   bool Init(const string symbol)
     {
      m_symbol=symbol;
      return (symbol!="");
     }

   double CurrentCompletedATR() const
     {
      const int available=Bars(m_symbol,SMC_EXECUTION_TF);
      const int requested=MathMin(available,SMC_ATR_PERIOD+4);
      if(requested<SMC_ATR_PERIOD+2)
         return 0.0;
      MqlRates rates[];
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_EXECUTION_TF,0,requested,rates);
      if(copied<SMC_ATR_PERIOD+2)
         return 0.0;
      ArrayResize(rates,copied);
      const int closed_end=copied-2;
      return ATRAt(rates,closed_end,SMC_ATR_PERIOD);
     }

   bool FindPostSweepEvent(const SMC_SWEEP_EVENT &sweep,
                           SMC_DISPLACEMENT_EVENT &output) const
     {
      ZeroMemory(output);
      output.direction=SMC_DIRECTION_NONE;
      if(!sweep.valid || sweep.direction==SMC_DIRECTION_NONE)
         return false;

      const int available=Bars(m_symbol,SMC_EXECUTION_TF);
      const int requested=MathMin(available,SMC_MAX_LIQUIDITY_AGE_BARS+SMC_ATR_PERIOD+SMC_SWING_SPAN+8);
      if(requested<SMC_ATR_PERIOD+SMC_SWING_SPAN*2+5)
         return false;
      MqlRates rates[];
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_EXECUTION_TF,0,requested,rates);
      if(copied<SMC_ATR_PERIOD+SMC_SWING_SPAN*2+5)
         return false;
      ArrayResize(rates,copied);
      const int closed_end=copied-2;

      double internal_high=0.0,internal_low=0.0;
      datetime internal_high_confirm=0,internal_low_confirm=0;
      const int timeframe_seconds=PeriodSeconds(SMC_EXECUTION_TF);
      if(timeframe_seconds<=0)
         return false;
      const datetime timeout=sweep.confirmation_time+12*PeriodSeconds(SMC_EXECUTION_TF);

      for(int index=SMC_ATR_PERIOD+SMC_SWING_SPAN*2;index<=closed_end;index++)
        {
         if(rates[index].time<sweep.confirmation_time)
            continue;
         if(rates[index].time+timeframe_seconds>timeout)
            break;

         const bool breaks_internal=(sweep.direction==SMC_DIRECTION_BUY ?
            (internal_high>0.0 && internal_high_confirm<=rates[index].time && rates[index].close>internal_high) :
            (internal_low>0.0 && internal_low_confirm<=rates[index].time && rates[index].close<internal_low));
         if(breaks_internal)
           {
            double body=0.0,ratio=0.0,location=0.0;
            const double atr=ATRAt(rates,index,SMC_ATR_PERIOD);
            if(IsDisplacement(rates[index],atr,sweep.direction,body,ratio,location))
              {
               output.valid=true;
               output.direction=sweep.direction;
               output.displacement_bar_time=rates[index].time;
               output.broken_level=(sweep.direction==SMC_DIRECTION_BUY ? internal_high : internal_low);
               output.atr_value=atr;
               output.body_size=body;
               output.body_range_ratio=ratio;
               output.close_location=location;
               output.confirmation_time=rates[index].time+timeframe_seconds;
               return true;
              }
           }

         // A pivot must itself form after reclaim and be confirmed before its break.
         const int pivot_index=index-SMC_SWING_SPAN;
         if(pivot_index>=SMC_SWING_SPAN && pivot_index+SMC_SWING_SPAN<=index &&
            rates[pivot_index].time>=sweep.confirmation_time)
           {
            if(sweep.direction==SMC_DIRECTION_BUY && IsPivotHigh(rates,pivot_index))
              {
               internal_high=rates[pivot_index].high;
               internal_high_confirm=rates[index].time+timeframe_seconds;
              }
            if(sweep.direction==SMC_DIRECTION_SELL && IsPivotLow(rates,pivot_index))
              {
               internal_low=rates[pivot_index].low;
               internal_low_confirm=rates[index].time+timeframe_seconds;
              }
           }
        }
      return false;
     }
  };

#endif
