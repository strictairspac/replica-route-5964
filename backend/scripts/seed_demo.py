from sqlalchemy import select,func
from src.config.database import Session,Base,engine
from src.models.entities import User,Node,RequestSet,Trial,BookingRun
from src.utils.security import password_hash

BOOKING_ROOMS=['east-hall','west-hall','north-lab']
BOOKING_EVENTS=[
 {'at_ms':0,'kind':'inspect','session':'shift-A','room':'east-hall','start_ms':0,'end_ms':3600000},
 {'at_ms':1000,'kind':'inspect','session':'shift-B','room':'east-hall','start_ms':1800000,'end_ms':5400000},
 {'at_ms':3000,'kind':'register','session':'shift-B','room':'east-hall','start_ms':1800000,'end_ms':5400000,'attempt':1},
 {'at_ms':4000,'kind':'register','session':'shift-A','room':'east-hall','start_ms':0,'end_ms':3600000,'attempt':1},
 {'at_ms':6000,'kind':'retry','session':'shift-A','room':'east-hall','start_ms':0,'end_ms':3600000,'attempt':1},
 {'at_ms':7000,'kind':'register','session':'shift-C','room':'west-hall','start_ms':0,'end_ms':3600000,'attempt':1},
 {'at_ms':8000,'kind':'register','session':'shift-A','room':'east-hall','start_ms':5400000,'end_ms':7200000,'attempt':2},
 {'at_ms':12000,'kind':'replicate','node':'south-replica','position':107,'delay_ms':20},
 {'at_ms':13000,'kind':'replica_read','session':'shift-A','room':'east-hall'},
 {'at_ms':14000,'kind':'replica_read','session':'shift-C','room':'west-hall'},
]

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@qiyuan.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=password_hash('Demo2026!'),role=role))
        for name,role,pos,delay,note in [('harbor-primary','primary',105,0,'栖原主站 · 写入航道'),('north-replica','replica',104,150,'北站副本 · 延迟偏高'),('south-replica','replica',105,20,'南站副本 · 常用读航道')]:
            if not db.scalar(select(Node).where(Node.name==name)):db.add(Node(name=name,role=role,status='healthy',lineage='qiyuan-log-01',position=pos,delay_ms=delay,note=note))
        mixed=[{'at_ms':0,'kind':'write','session':'lab-A'},{'at_ms':100,'kind':'read','session':'lab-A'},{'at_ms':600,'kind':'replicate','node':'south-replica','position':106,'delay_ms':20},{'at_ms':700,'kind':'read','session':'lab-A'},{'at_ms':800,'kind':'node_down','node':'south-replica'},{'at_ms':900,'kind':'read','session':'lab-A'},{'at_ms':1000,'kind':'node_down','node':'harbor-primary'},{'at_ms':1100,'kind':'read','session':'lab-A'}]
        cases=[('QY-1117-A','交接班写后读与双节点故障',mixed),('QY-1117-B','延迟超标回退主库',[{'at_ms':0,'kind':'node_down','node':'south-replica'},{'at_ms':10,'kind':'read','session':'visitor'}]),('QY-1117-C','双副本不可读时拒绝',[{'at_ms':0,'kind':'write','session':'lab-B'},{'at_ms':1000,'kind':'node_down','node':'harbor-primary'},{'at_ms':1100,'kind':'read','session':'lab-B'}])]
        for code,title,events in cases:
            if not db.scalar(select(RequestSet).where(RequestSet.code==code)):db.add(RequestSet(code=code,title=title,events=events,note='栖原站点值班交接 · 09:15 请求轨迹'))
        db.commit()
        for name,strategy in (('shift-booking-recheck','recheck'),('shift-booking-hold','hold')):
            if db.scalar(select(BookingRun).where(BookingRun.name==name)):continue
            from src.services.bookings import simulate
            nodes=[{'name':n,'role':r,'status':'healthy','lineage':'qiyuan-log-01','position':p,'delay_ms':d} for n,r,p,d in [('harbor-primary','primary',105,0),('north-replica','replica',104,150),('south-replica','replica',105,20)]]
            snapshot={'rooms':BOOKING_ROOMS,'events':BOOKING_EVENTS,'nodes':nodes}
            record_=BookingRun(name=name,strategy=strategy,rooms=BOOKING_ROOMS,events=BOOKING_EVENTS,hold_ttl_ms=600000,wait_max_ms=300000,max_delay_ms=100,sticky_ms=500,status='completed',input_snapshot=snapshot)
            record_.result=simulate(BOOKING_ROOMS,BOOKING_EVENTS,strategy,nodes)
            db.add(record_);db.commit()
        print({cls.__tablename__:db.scalar(select(func.count()).select_from(cls)) for cls in (User,Node,RequestSet,Trial,BookingRun)})
if __name__=='__main__':seed()
