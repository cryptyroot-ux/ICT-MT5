#ifndef SMC_LOGGER_MQH
#define SMC_LOGGER_MQH

#include <SMC_Config.mqh>

enum SMC_LOG_LEVEL
  {
   SMC_LOG_ERROR=0,
   SMC_LOG_INFO=1,
   SMC_LOG_DEBUG=2
  };

class CSMCLogger
  {
private:
   string        m_symbol;
   string        m_file_name;
   string        m_config_hash;
   string        m_variant;
   string        m_version;
   bool          m_observe_only;
   SMC_LOG_LEVEL m_level;

   string HashConfig(const string canonical) const
     {
      uint hash=2166136261;
      const int length=StringLen(canonical);
      for(int index=0;index<length;index++)
        {
         hash^=(uint)StringGetCharacter(canonical,index);
         hash*=16777619;
        }
      return StringFormat("fnv1a32-%08X",hash);
     }

public:
                     CSMCLogger()
     {
      m_symbol="";
      m_file_name=SMC_V2_EVENT_LOG;
      m_config_hash="";
      m_level=SMC_LOG_INFO;
     }

   bool Init(const string symbol,const long magic,const double risk_percent,
             const double spread_limit,const double commission,const double slippage,
             const bool cost_verified,const bool observe_only,const SMC_LOG_LEVEL level)
     {
      m_symbol=symbol;
      m_level=level;
      m_variant=SMC_V2_VARIANT;
      m_version=SMC_V2_VERSION;
      m_observe_only=observe_only;
      const string canonical=StringFormat("%s|%s|%s|%s|%I64d|%.4f|%.4f|%.4f|%.4f|%d|%d",
         SMC_STRATEGY_NAME,m_version,m_symbol,m_variant,magic,risk_percent,
         spread_limit,commission,slippage,(int)cost_verified,(int)observe_only);
      m_config_hash=HashConfig(canonical);

      const int handle=FileOpen(m_file_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ,',');
      if(handle==INVALID_HANDLE)
         return false;
      if(FileSize(handle)==0)
         FileWrite(handle,"server_time","symbol","strategy","version","variant","config_hash",
                   "setup_id","event","state_before","state_after","direction","h1_bos_level",
                   "h1_bos_time","liquidity_level","sweep_extreme","sweep_time","sweep_confirm_time",
                   "choch_level","displacement_bar_time","choch_confirmation_time",
                   "fvg_lower","fvg_upper","fvg_creation_time","entry",
                   "target_liquidity_time","sl","tp","atr14_m5","spread","risk_percent","volume","expected_risk",
                   "net_rr","order_ticket","deal_id","position_id","fill_price","slippage",
                   "retcode","actual_pnl","commission",
                   "swap","fee","reason");
      FileSeek(handle,0,SEEK_END);
      FileClose(handle);
      return true;
     }

   string ConfigHash() const { return m_config_hash; }
   string Variant() const { return m_variant; }
   string LogFileName() const { return m_file_name; }

   void Write(const SMC_SETUP &setup,const string event,const string state_before,
              const double spread,const double risk_percent,const int retcode,
              const string reason,const double actual_pnl=0.0,const double commission=0.0,
              const double swap=0.0,const double fee=0.0) const
     {
      if(m_level==SMC_LOG_ERROR && reason=="")
         return;
      const int handle=FileOpen(m_file_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ,',');
      if(handle==INVALID_HANDLE)
        {
         Print("[SMC] log_open_failed file=",m_file_name," event=",event);
         return;
        }
      FileSeek(handle,0,SEEK_END);
      datetime now=TimeTradeServer();
      if(now<=0) now=TimeCurrent();
      FileWrite(handle,TimeToString(now,TIME_DATE|TIME_SECONDS),m_symbol,SMC_STRATEGY_NAME,
                m_version,m_variant,m_config_hash,setup.setup_id,event,state_before,
                SMCStateName(setup.state),SMCDirectionName(setup.direction),
                DoubleToString(setup.h1_bos_level,8),TimeToString(setup.h1_bos_confirmation,TIME_DATE|TIME_SECONDS),
                DoubleToString(setup.liquidity_level,8),DoubleToString(setup.sweep_extreme,8),
                TimeToString(setup.sweep_time,TIME_DATE|TIME_SECONDS),
                TimeToString(setup.sweep_confirmation,TIME_DATE|TIME_SECONDS),
                DoubleToString(setup.choch_level,8),
                TimeToString(setup.displacement_bar_time,TIME_DATE|TIME_SECONDS),
                TimeToString(setup.choch_confirmation,TIME_DATE|TIME_SECONDS),
                DoubleToString(setup.fvg_lower,8),DoubleToString(setup.fvg_upper,8),
                TimeToString(setup.fvg_creation,TIME_DATE|TIME_SECONDS),
                DoubleToString(setup.entry_price,8),
                TimeToString(setup.target_liquidity_time,TIME_DATE|TIME_SECONDS),
                DoubleToString(setup.stop_loss,8),
                DoubleToString(setup.take_profit,8),DoubleToString(setup.atr_value,8),
                DoubleToString(spread,8),DoubleToString(risk_percent,4),DoubleToString(setup.volume,8),
                DoubleToString(setup.expected_risk,8),DoubleToString(setup.expected_net_rr,4),
                (long)setup.order_ticket,(long)setup.deal_id,(long)setup.position_id,
                DoubleToString(setup.actual_fill_price,8),DoubleToString(setup.actual_slippage,8),retcode,
                DoubleToString(actual_pnl,8),DoubleToString(commission,8),
                DoubleToString(swap,8),DoubleToString(fee,8),reason);
      FileFlush(handle);
      FileClose(handle);
      if(m_level>=SMC_LOG_INFO)
         Print("[SMC] ",event," setup=",setup.setup_id," state=",SMCStateName(setup.state),
               " reason=",reason," retcode=",retcode);
     }
  };

#endif
