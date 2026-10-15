#ifndef SMC_MARKET_STRUCTURE_MQH
#define SMC_MARKET_STRUCTURE_MQH

#include <SMC_Config.mqh>

class CSMCMarketStructure
  {
private:
   string             m_symbol;
   datetime           m_forming_bar_time;
   SMC_STRUCTURE_EVENT m_last_bos;
   SMC_DIRECTION      m_bias;
   bool               m_ready;

   bool IsPivotHigh(const MqlRates &rates[],const int index,const int span) const
     {
      const double level=rates[index].high;
      for(int offset=1;offset<=span;offset++)
        {
         if(rates[index-offset].high>=level || rates[index+offset].high>=level)
            return false;
        }
      return true;
     }

   bool IsPivotLow(const MqlRates &rates[],const int index,const int span) const
     {
      const double level=rates[index].low;
      for(int offset=1;offset<=span;offset++)
        {
         if(rates[index-offset].low<=level || rates[index+offset].low<=level)
            return false;
        }
      return true;
     }

public:
                     CSMCMarketStructure()
     {
      m_symbol="";
      m_forming_bar_time=0;
      m_bias=SMC_DIRECTION_NONE;
      m_ready=false;
      ZeroMemory(m_last_bos);
      m_last_bos.direction=SMC_DIRECTION_NONE;
     }

   bool Init(const string symbol)
     {
      m_symbol=symbol;
      m_forming_bar_time=0;
      m_bias=SMC_DIRECTION_NONE;
      m_ready=false;
      ZeroMemory(m_last_bos);
      m_last_bos.direction=SMC_DIRECTION_NONE;
      return (symbol!="");
     }

   bool Refresh()
     {
      const int available=Bars(m_symbol,SMC_CONTEXT_TF);
      if(available<SMC_SWING_SPAN*2+5)
        {
         m_ready=false;
         m_bias=SMC_DIRECTION_NONE;
         return false;
        }

      const int requested=MathMin(available,12000);
      MqlRates rates[];
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_CONTEXT_TF,0,requested,rates);
      if(copied<SMC_SWING_SPAN*2+5)
        {
         m_ready=false;
         m_bias=SMC_DIRECTION_NONE;
         return false;
        }
      ArrayResize(rates,copied);
      const datetime forming_time=rates[copied-1].time;
      if(forming_time==m_forming_bar_time && m_ready)
         return true;
      m_forming_bar_time=forming_time;

      const int closed_end=copied-2;
      double last_high=0.0,last_low=0.0;
      datetime last_high_time=0,last_low_time=0;
      datetime last_high_confirm=0,last_low_confirm=0;
      datetime broken_high_time=0,broken_low_time=0;
      const int timeframe_seconds=PeriodSeconds(SMC_CONTEXT_TF);
      if(timeframe_seconds<=0)
         return false;
      m_bias=SMC_DIRECTION_NONE;
      ZeroMemory(m_last_bos);
      m_last_bos.direction=SMC_DIRECTION_NONE;

      for(int index=SMC_SWING_SPAN*2;index<=closed_end;index++)
        {
         bool bullish_break=(last_high>0.0 && rates[index].time>=last_high_confirm &&
                             last_high_time!=broken_high_time && rates[index].close>last_high);
         bool bearish_break=(last_low>0.0 && rates[index].time>=last_low_confirm &&
                             last_low_time!=broken_low_time && rates[index].close<last_low);

         if(bullish_break && bearish_break)
           {
            m_bias=SMC_DIRECTION_NONE;
            m_last_bos.direction=SMC_DIRECTION_NONE;
           }
         else if(bullish_break)
           {
            broken_high_time=last_high_time;
            m_bias=SMC_DIRECTION_BUY;
            m_last_bos.direction=SMC_DIRECTION_BUY;
            m_last_bos.level=last_high;
            m_last_bos.pivot_time=last_high_time;
            m_last_bos.confirmation_time=rates[index].time+timeframe_seconds;
           }
         else if(bearish_break)
           {
            broken_low_time=last_low_time;
            m_bias=SMC_DIRECTION_SELL;
            m_last_bos.direction=SMC_DIRECTION_SELL;
            m_last_bos.level=last_low;
            m_last_bos.pivot_time=last_low_time;
            m_last_bos.confirmation_time=rates[index].time+timeframe_seconds;
           }

         // Pivot at index-span becomes confirmed on this completed close.
         const int pivot_index=index-SMC_SWING_SPAN;
         if(pivot_index>=SMC_SWING_SPAN && pivot_index+SMC_SWING_SPAN<=closed_end)
           {
            if(IsPivotHigh(rates,pivot_index,SMC_SWING_SPAN))
              {
               last_high=rates[pivot_index].high;
               last_high_time=rates[pivot_index].time;
               last_high_confirm=rates[index].time+timeframe_seconds;
              }
            if(IsPivotLow(rates,pivot_index,SMC_SWING_SPAN))
              {
               last_low=rates[pivot_index].low;
               last_low_time=rates[pivot_index].time;
               last_low_confirm=rates[index].time+timeframe_seconds;
              }
           }
        }
      m_ready=(m_bias!=SMC_DIRECTION_NONE && m_last_bos.confirmation_time>0);
      return m_ready;
     }

   bool HasBias() const { return m_ready; }
   SMC_DIRECTION Bias() const { return m_ready ? m_bias : SMC_DIRECTION_NONE; }
   SMC_STRUCTURE_EVENT LastBOS() const { return m_last_bos; }
   string Symbol() const { return m_symbol; }
  };

#endif
