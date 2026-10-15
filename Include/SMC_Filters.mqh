//+------------------------------------------------------------------+
//|  SMC_Filters.mqh                                                 |
//|  Filter V2 yang di-port dari desain ICT_EA upstream.             |
//|                                                                  |
//|  Dibuat setelah diagnosis V1 menunjukkan sinyal_SIGNIFIKAN        |
//|  anti-predictif (P(MFE>MAE) 43.6%, z=-4.23) dan losses di semua  |
//|  level target bahkan pada biaya nol.                             |
//|                                                                  |
//|  Tiga filter:                                                    |
//|   1. Lantai absolut SL          -> biaya tidak memakan 1R       |
//|   2. Jendela sesi high-conviction -> jam diUKUR dari volatilitas |
//|   3. Premium/discount D1        -> komponen yang membalikkan tanda|
//+------------------------------------------------------------------+
#ifndef SMC_FILTERS_MQH
#define SMC_FILTERS_MQH

//--- Dipreregistrasi pada studi DEV 2018-07..2024-04 sebelum melihat hasil.
#define SMC_V2_MIN_SL_PRICE        20.0   // USD/oz; biaya round-turn ~$0.43
#define SMC_V2_ATR_SL_MULTIPLIER    3.0
#define SMC_V2_SESSION_RATIO        1.30   // sama dengan ambang bobot gold di ICT_EA
#define SMC_V2_FIXED_TP_R           3.0   // TP tetap 3R, bukan next-liquidity-target


//---Proteksi final V2 ---------------------------------------------------------
//| Satu-satunya sumber kebenaran SL/TP V2. Rumus mentah B3 (extremum sweep +/-0.5
//| ATR) TIDAK boleh dipakai di sini: SELL memakai tanda PLUS sehingga stop berada
//| di atas high sweep, dan jarak $SMC_V2_MIN_SL_PRICE harus memperlebar SL ke arah
//| proteksi (BUY ke bawah, SELL ke atas).
bool SMC_ProtectionLevels(const SMC_DIRECTION direction,const double entry,
                          const double sweep_extreme,const double atr_value,
                          const double tick_size,double &stop,double &target)
  {
   stop=0.0;
   target=0.0;
   if(direction!=SMC_DIRECTION_BUY && direction!=SMC_DIRECTION_SELL)
      return false;
   if(entry<=0.0 || atr_value<=0.0 || tick_size<=0.0 || !MathIsValidNumber(entry) ||
      !MathIsValidNumber(sweep_extreme) || !MathIsValidNumber(atr_value))
      return false;

   const bool is_buy=(direction==SMC_DIRECTION_BUY);
   const double raw_stop=(is_buy ? sweep_extreme-0.5*atr_value : sweep_extreme+0.5*atr_value);
   const double raw_distance=MathAbs(entry-raw_stop);
   const double floor_distance=MathMax(SMC_V2_MIN_SL_PRICE,SMC_V2_ATR_SL_MULTIPLIER*atr_value);
   const double distance=MathMax(raw_distance,floor_distance);
   if(distance<=0.0)
      return false;

   const double stop_price=(is_buy ? entry-distance : entry+distance);
   const double target_price=(is_buy ? entry+SMC_V2_FIXED_TP_R*distance
                                     : entry-SMC_V2_FIXED_TP_R*distance);
   stop=SMCRoundToTick(stop_price,tick_size);
   target=SMCRoundToTick(target_price,tick_size);
   return (stop>0.0 && target>0.0);
  }

//--- Self-test golden vector (dipakai saat init, sebelum EA boleh berjalan) ----
//| Nilai yang sama persis dengan research/v2_parity.protection_levels, jadi
//| regresi pembulatan atau tanda di MQL langsung ketahuan.
bool SMC_V2ProtectionSelfTest()
  {
   double buy_stop=0.0,buy_target=0.0,sell_stop=0.0,sell_target=0.0;
   if(!SMC_ProtectionLevels(SMC_DIRECTION_BUY,2000.0,1995.0,2.0,0.01,buy_stop,buy_target))
      return false;
   if(!SMC_ProtectionLevels(SMC_DIRECTION_SELL,2000.0,2005.0,2.0,0.01,sell_stop,sell_target))
      return false;
   const double tolerance=0.0001;
   return (MathAbs(buy_stop-1980.0)<tolerance && MathAbs(buy_target-2060.0)<tolerance &&
           MathAbs(sell_stop-2020.0)<tolerance && MathAbs(sell_target-1940.0)<tolerance);
  }

//--- Filter sesi ---------------------------------------------------------------
//| Ambang 1.30x rerata(range) per jam. Bila tidak ada jam yang lolos, ambang
//| itu akan men-disable EA sepenuhnya, jadi gagal itu dilaporkan lewat nilainya
//| sendiri dan TIDAK disamarkan jadi "semua jam".
//| Jendela hanya bar M5 yang SUDAH tertutup (shift 1..200). Versi lama memakai
//| shift 0 sehingga bar yang masihforming ikut masuk dan tidak paritas dengan
//| model Python.
bool SMC_SessionAllowed(const string symbol,const datetime decision_bar_time)
  {
   if(decision_bar_time<=0)
      return false;

   MqlRates rates[];
   const int needed=200;
   const int copied=CopyRates(symbol,PERIOD_M5,1,needed,rates);
   if(copied<needed)
      return false;

   ArraySetAsSeries(rates,true);
   double sum[24];
   int count[24];
   for(int i=0;i<24;i++) { sum[i]=0.0; count[i]=0; }

   for(int i=0;i<copied;i++)
     {
      MqlDateTime dt;
      TimeToStruct(rates[i].time,dt);
      const double range=rates[i].high-rates[i].low;
      sum[dt.hour]+=range;
      count[dt.hour]++;
     }

   double total=0.0;
   int total_count=0;
   double hour_mean[24];
   for(int h=0;h<24;h++)
     {
      if(count[h]==0) { hour_mean[h]=0.0; continue; }
      hour_mean[h]=sum[h]/count[h];
      total+=hour_mean[h];
      total_count++;
     }
   if(total_count==0)
      return false;

   const double mean=total/total_count;
   MqlDateTime decision;
   TimeToStruct(decision_bar_time,decision);
   if(decision.hour<0 || decision.hour>23)
      return false;
   return (hour_mean[decision.hour]>=mean*SMC_V2_SESSION_RATIO);
  }

//--- Cache sesi per bar keputusan ---------------------------------------------
//| Jendela 200 bar tidak berubah di dalam satu bar M5, jadi nilainya dihitung
//| satu kali per bar keputusan. Tanpa cache, filter ini membaca 200 bar pada
//| setiap tick.
datetime g_smc_session_cache_bar=0;
bool     g_smc_session_cache_valid=false;
bool     g_smc_session_cache_value=false;

bool SMC_SessionAllowedCached(const string symbol,const datetime decision_bar_time)
  {
   if(decision_bar_time<=0)
      return false;
   if(g_smc_session_cache_valid && g_smc_session_cache_bar==decision_bar_time)
      return g_smc_session_cache_value;
   g_smc_session_cache_value=SMC_SessionAllowed(symbol,decision_bar_time);
   g_smc_session_cache_bar=decision_bar_time;
   g_smc_session_cache_valid=true;
   return g_smc_session_cache_value;
  }

//--- Premium/discount D1 -------------------------------------------------------
//| BUY hanya di bawah 50% range hari berjalan; SELL hanya di atas.
//| Bar D1 diambil langsung dari terminal, bukan disintesis dari M5.
bool SMC_PremiumDiscountAllows(const string symbol,const bool is_buy,const double price)
  {
   MqlRates daily[];
   ArraySetAsSeries(daily,true);
   // index 1 = bar D1 yang sudah tertutup (bar berjalan belum final)
   const int copied=CopyRates(symbol,PERIOD_D1,1,1,daily);
   if(copied<1)
      return false;
   if(daily[0].high<=daily[0].low)
      return false;
   const double mid=(daily[0].high+daily[0].low)*0.5;
   return is_buy ? (price<mid) : (price>mid);
  }

//--- Warm-up properti simbol --------------------------------------------------
//| SYMBOL_TRADE_TICK_VALUE dan sejenisnya bisa 0 pada OnInit pertama karena
//| spec symbol belum selesai dimuat. Menolak EA saat itu keliru: yang terjadi
//| adalah init prematur, bukan konfigurasi salah.
//| Mengembalikan true kalau semua properti sudah valid.
bool SMCWaitForSymbolProperties(const string symbol,const int timeout_ms)
  {
   const int step_ms=250;
   const int max_steps=MathMax(1,timeout_ms/step_ms);
   for(int attempt=0;attempt<max_steps;attempt++)
     {
      double tick_size=0.0,tick_value=0.0,volume_min=0.0,volume_max=0.0,volume_step=0.0,point=0.0;
      if(SymbolSelect(symbol,true) &&
         SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) && tick_size>0.0 &&
         SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE,tick_value) && tick_value>0.0 &&
         SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN,volume_min) && volume_min>0.0 &&
         SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX,volume_max) && volume_max>=volume_min &&
         SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP,volume_step) && volume_step>0.0 &&
         SymbolInfoDouble(symbol,SYMBOL_POINT,point) && point>0.0)
         return true;
      Sleep(step_ms);
     }
   return false;
  }

//--- Diagnostik properti (dipakai saat validasi gagal) ------------------------
string SMCDescribeSymbolProperties(const string symbol)
  {
   double tick_size=0.0,tick_value=0.0,volume_min=0.0,volume_max=0.0,volume_step=0.0,point=0.0;
   const bool ok_size=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE,tick_size);
   const bool ok_val=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE,tick_value);
   const bool ok_min=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN,volume_min);
   const bool ok_max=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX,volume_max);
   const bool ok_step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP,volume_step);
   const bool ok_point=SymbolInfoDouble(symbol,SYMBOL_POINT,point);
   return StringFormat("tick_size=%.8f(%d) tick_value=%.6f(%d) vol_min=%.4f(%d) "
                       "vol_max=%.4f(%d) vol_step=%.4f(%d) point=%.8f(%d)",
                       tick_size,(int)ok_size,tick_value,(int)ok_val,volume_min,(int)ok_min,
                       volume_max,(int)ok_max,volume_step,(int)ok_step,point,(int)ok_point);
  }

#endif // SMC_FILTERS_MQH