//+------------------------------------------------------------------+
//|  SMC_RuntimeConfig.mqh                                           |
//|  Override input EA dari file MQL5/Files/SMC_V2.cfg               |
//|                                                                  |
//|  Kenapa perlu: input yang ditulis di chart .chr tidak selalu    |
//|  diteruskan MT5 ke EA (terbukti: nilai tetap default meski      |
//|  .chr sudah benar). Membaca file.cfg memberi jalur konfigurasi   |
//|  yang deterministik dan bisa diaudit, sama seperti              |
//|  EA_Guarded_Stub memakai EA_Guarded_Stub.cfg.                    |
//|                                                                  |
//|  Isi file: KEY=VALUE satu per baris, '#' = komentar.             |
//+------------------------------------------------------------------+
#ifndef SMC_RUNTIME_CONFIG_MQH
#define SMC_RUNTIME_CONFIG_MQH

//+------------------------------------------------------------------+
//| Terapkan override ke input. Nilai input tetap jadi default.     |
//+------------------------------------------------------------------+
void SMCApplyRuntimeConfig(string &symbol,long &magic,double &risk_percent,
                           double &daily_loss_percent,bool &cost_model_verified,
                           bool &observe_only,
                           double &commission_per_lot,double &slippage_per_lot,
                           int &log_level)
  {
   string path=SMC_RUNTIME_CONFIG_NAME;
   if(!FileIsExist(path))
      return;

   int handle=FileOpen(path,FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle==INVALID_HANDLE)
      return;

   while(!FileIsEnding(handle))
     {
      string line=FileReadString(handle);
      const int comment=StringFind(line,"#");
      if(comment>=0) line=StringSubstr(line,0,comment);
      StringTrimLeft(line); StringTrimRight(line);
      if(StringLen(line)==0) continue;
      const int eq=StringFind(line,"=");
      if(eq<0) continue;
      string key=StringSubstr(line,0,eq);
      string value=StringSubstr(line,eq+1);
      StringTrimLeft(value); StringTrimRight(value);

      if(key=="SYMBOL")            symbol=value;
      else if(key=="MAGIC")        magic=(long)StringToInteger(value);
      else if(key=="RISK_PCT")     risk_percent=StringToDouble(value);
      else if(key=="DAILY_LOSS_PCT") daily_loss_percent=StringToDouble(value);
      else if(key=="COST_VERIFIED")cost_model_verified=(StringToDouble(value)!=0.0);
      else if(key=="OBSERVE_ONLY") observe_only=(StringToDouble(value)!=0.0);
      else if(key=="COMMISSION_PER_LOT") commission_per_lot=StringToDouble(value);
      else if(key=="SLIPPAGE_PER_LOT")   slippage_per_lot=StringToDouble(value);
      else if(key=="LOG_LEVEL")     log_level=(int)StringToInteger(value);
     }
   FileClose(handle);
  }

#endif // SMC_RUNTIME_CONFIG_MQH
