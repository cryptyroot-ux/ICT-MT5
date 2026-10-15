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

//--- Lantai SL -----------------------------------------------------------------
//| Kembalikan jarak SL yang dimeulkan. Memperlebar SL hanya aman ke arah
//| proteksi (BUY ke bawah, SELL ke atas).
double SMC_WidenedStopDistance(const double sweep_distance,const double atr_value)
  {
   const double floor_price=MathMax(SMC_V2_MIN_SL_PRICE,SMC_V2_ATR_SL_MULTIPLIER*atr_value);
   return MathMax(sweep_distance,floor_price);
  }

//--- Filter sesi ---------------------------------------------------------------
//| Ambang 1.30x rerata(range) per jam. Bila tidak ada jam yang lolos, ambang
//| itu akan men-disable EA sepenuhnya, jadi gagal itu dilaporkan lewat nilainya
//| sendiri dan TIDAK disamarkan jadi "semua jam".
bool SMC_SessionAllowed(const string symbol)
  {
   MqlRates rates[];
   const int needed=200;
   const int copied=CopyRates(symbol,PERIOD_M5,0,needed,rates);
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
   MqlDateTime now;
   TimeToStruct(TimeCurrent(),now);
   if(now.hour<0 || now.hour>23)
      return false;
   return (hour_mean[now.hour]>=mean*SMC_V2_SESSION_RATIO);
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