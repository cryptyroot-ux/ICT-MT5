#ifndef SMC_LIQUIDITY_MQH
#define SMC_LIQUIDITY_MQH

#include <SMC_Config.mqh>

class CSMCLiquidity
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

   bool LoadRates(MqlRates &rates[],int &closed_end) const
     {
      const int available=Bars(m_symbol,SMC_EXECUTION_TF);
      if(available<SMC_MAX_LIQUIDITY_AGE_BARS+SMC_SWING_SPAN+3)
         return false;
      const int requested=MathMin(available,SMC_MAX_LIQUIDITY_AGE_BARS+SMC_SWING_SPAN+8);
      ArrayResize(rates,requested);
      ArraySetAsSeries(rates,false);
      const int copied=CopyRates(m_symbol,SMC_EXECUTION_TF,0,requested,rates);
      if(copied<SMC_SWING_SPAN*2+4)
         return false;
      ArrayResize(rates,copied);
      closed_end=copied-2; // Last rate is the unfinished M5 candle.
      return (closed_end>=SMC_SWING_SPAN*2+1);
     }

public:
                     CSMCLiquidity() { m_symbol=""; }

   bool Init(const string symbol)
     {
      m_symbol=symbol;
      return (symbol!="");
     }

   bool DetectSweep(const SMC_DIRECTION direction,const datetime after_time,
                    SMC_SWEEP_EVENT &output) const
     {
      ZeroMemory(output);
      output.valid=false;
      output.direction=SMC_DIRECTION_NONE;
      if(direction==SMC_DIRECTION_NONE)
         return false;

      double tick_size=0.0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0)
         return false;
      MqlRates rates[];
      int closed_end=-1;
      if(!LoadRates(rates,closed_end))
         return false;

      const int first_sweep=MathMax(SMC_SWING_SPAN*2+1,closed_end-SMC_MAX_LIQUIDITY_AGE_BARS+1);
      const int timeframe_seconds=PeriodSeconds(SMC_EXECUTION_TF);
      if(timeframe_seconds<=0)
         return false;
      datetime best_confirmation=0;
      bool ambiguous=false;
      int matched=0;

      for(int sweep_index=first_sweep;sweep_index<=closed_end;sweep_index++)
        {
         if(rates[sweep_index].time<after_time)
            continue;
         const int first_pivot=MathMax(SMC_SWING_SPAN,sweep_index-SMC_MAX_LIQUIDITY_AGE_BARS-SMC_SWING_SPAN);
         for(int pivot_index=first_pivot;pivot_index+SMC_SWING_SPAN<sweep_index;pivot_index++)
           {
            const datetime pivot_confirmation=rates[pivot_index+SMC_SWING_SPAN].time+timeframe_seconds;
            if(pivot_confirmation>rates[sweep_index].time)
               continue; // Pool confirmation must precede the excursion.
            if(sweep_index-(pivot_index+SMC_SWING_SPAN)>SMC_MAX_LIQUIDITY_AGE_BARS)
               continue;

            double level=0.0;
            bool excursion=false;
            if(direction==SMC_DIRECTION_BUY && IsPivotLow(rates,pivot_index))
              {
               level=rates[pivot_index].low;
               excursion=(rates[sweep_index].low<=level-SMC_SWEEP_THRESHOLD_TICKS*tick_size);
              }
            else if(direction==SMC_DIRECTION_SELL && IsPivotHigh(rates,pivot_index))
              {
               level=rates[pivot_index].high;
               excursion=(rates[sweep_index].high>=level+SMC_SWEEP_THRESHOLD_TICKS*tick_size);
              }
            if(!excursion)
               continue;

            const int reclaim_end=MathMin(closed_end,sweep_index+SMC_MAX_RECLAIM_BARS);
            double extreme=(direction==SMC_DIRECTION_BUY ? rates[sweep_index].low : rates[sweep_index].high);
            int reclaim_index=-1;
            for(int index=sweep_index;index<=reclaim_end;index++)
              {
               if(direction==SMC_DIRECTION_BUY)
                  extreme=MathMin(extreme,rates[index].low);
               else
                  extreme=MathMax(extreme,rates[index].high);
               const bool reclaimed=(direction==SMC_DIRECTION_BUY ? rates[index].close>level : rates[index].close<level);
               if(reclaimed)
                 {
                  reclaim_index=index;
                  break;
                 }
              }
            if(reclaim_index<0)
               continue;

            const datetime confirmation_time=rates[reclaim_index].time+timeframe_seconds;
            if(best_confirmation==0 || confirmation_time<best_confirmation)
              {
               best_confirmation=confirmation_time;
               output.valid=true;
               output.direction=direction;
               output.level=level;
               output.extreme=extreme;
               output.pivot_time=rates[pivot_index].time;
               output.excursion_time=rates[sweep_index].time;
               output.confirmation_time=confirmation_time;
               output.setup_id=SMCMakeSetupId(m_symbol,direction,output.excursion_time,level,tick_size);
               matched=1;
               ambiguous=false;
              }
            else if(confirmation_time==best_confirmation && MathAbs(level-output.level)>SMC_EQUAL_LEVEL_TICKS*tick_size)
              {
               ambiguous=true;
               matched++;
              }
            else if(confirmation_time==best_confirmation && MathAbs(level-output.level)<=SMC_EQUAL_LEVEL_TICKS*tick_size && rates[pivot_index].time>output.pivot_time)
              {
               // Equal highs/lows share one pool; the latest confirmed pivot anchors it.
               output.level=level;
               output.pivot_time=rates[pivot_index].time;
               output.setup_id=SMCMakeSetupId(m_symbol,direction,output.excursion_time,level,tick_size);
              }
           }
        }

      if(!output.valid || ambiguous || matched>1 && ambiguous)
        {
         ZeroMemory(output);
         output.valid=false;
         output.direction=SMC_DIRECTION_NONE;
         return false;
        }
      return true;
     }

   bool FindNextLiquidity(const SMC_DIRECTION direction,const double entry,
                          double &target,datetime &pivot_time) const
     {
      target=0.0;
      pivot_time=0;
      if(direction==SMC_DIRECTION_NONE || entry<=0.0)
         return false;
      MqlRates rates[];
      int closed_end=-1;
      if(!LoadRates(rates,closed_end))
         return false;
      const int first_pivot=MathMax(SMC_SWING_SPAN,closed_end-SMC_MAX_LIQUIDITY_AGE_BARS-SMC_SWING_SPAN);
      double best_distance=DBL_MAX;
      for(int pivot_index=first_pivot;pivot_index+SMC_SWING_SPAN<=closed_end;pivot_index++)
        {
         const int confirmation_index=pivot_index+SMC_SWING_SPAN;
         if(closed_end-confirmation_index>SMC_MAX_LIQUIDITY_AGE_BARS)
            continue;
         double candidate=0.0;
         if(direction==SMC_DIRECTION_BUY && IsPivotHigh(rates,pivot_index))
            candidate=rates[pivot_index].high;
         else if(direction==SMC_DIRECTION_SELL && IsPivotLow(rates,pivot_index))
            candidate=rates[pivot_index].low;
         if(candidate<=0.0)
            continue;
         if((direction==SMC_DIRECTION_BUY && candidate<=entry) ||
            (direction==SMC_DIRECTION_SELL && candidate>=entry))
            continue;
         const double distance=MathAbs(candidate-entry);
         if(distance<best_distance)
           {
            best_distance=distance;
            target=candidate;
            pivot_time=rates[pivot_index].time;
           }
        }
      return (target>0.0);
     }
  };

#endif
