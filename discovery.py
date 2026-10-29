"""Locate a recorder on the directly attached LAN by its MAC address.

Uses ARP identity checks, never sends authentication data to unverified IPs.
Linux + host networking + CAP_NET_RAW are required for MAC discovery.
"""
import contextlib, fcntl, ipaddress, json, select, socket, struct, time
from pathlib import Path

class DiscoveryError(RuntimeError):pass

def normalize_mac(value):
    value=value.replace('-',':').lower()
    parts=value.split(':')
    if len(parts)!=6:raise DiscoveryError('Invalid recorder MAC address')
    try:values=[int(p,16) for p in parts]
    except ValueError:raise DiscoveryError('Invalid recorder MAC address')
    if any(v<0 or v>255 for v in values):raise DiscoveryError('Invalid recorder MAC address')
    return ':'.join('%02x'%v for v in values)

def interfaces():
    result=[];seen=set()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            f=line.split()
            if len(f)<8:continue
            name,dest,gateway,flags=f[:4]
            if name in seen or gateway!='00000000' or dest=='00000000' or not int(flags,16)&1:continue
            if name.startswith(('lo','docker','veth','br-','virbr','tailscale','tun','cni','flannel')):continue
            req=struct.pack('256s',name.encode()[:15])
            try:
                ip=socket.inet_ntoa(fcntl.ioctl(s,0x8915,req)[20:24])
                mask=socket.inet_ntoa(fcntl.ioctl(s,0x891b,req)[20:24])
                mac=fcntl.ioctl(s,0x8927,req)[18:24]
            except OSError:continue
            net=ipaddress.ip_network(ip+'/'+mask,strict=False)
            if net.num_addresses>4096:continue
            seen.add(name);result.append((name,ip,mac,net))
    if not result:raise DiscoveryError('找不到可探索的實體區網介面（支援最多 4096 位址的網段）')
    return result

def request_frame(source_mac,source_ip,target_ip):
    return (b'\xff'*6+source_mac+b'\x08\x06'+struct.pack('!HHBBH',1,0x0800,6,4,1)
            +source_mac+socket.inet_aton(source_ip)+bytes(6)+socket.inet_aton(target_ip))

def response_ip(frame,target_mac,our_mac,our_ip,network):
    if len(frame)<42 or frame[12:14]!=b'\x08\x06':return None
    if frame[14:22]!=struct.pack('!HHBBH',1,0x0800,6,4,2):return None
    if frame[22:28]!=target_mac or frame[32:38]!=our_mac or frame[38:42]!=socket.inet_aton(our_ip):return None
    address=socket.inet_ntoa(frame[28:32])
    if ipaddress.ip_address(address) not in network:return None
    return address

def resolve_device(config,force_scan=False):
    mac=config.get('device_mac')
    if not mac:return config['host']
    mac=normalize_mac(mac)
    if len(mac)!=17:raise DiscoveryError('Invalid recorder MAC address')
    target=bytes.fromhex(mac.replace(':',''))
    cache=Path(config.get('discovery_cache','state/device-address.json'))
    hints=[]
    if not force_scan:
        with contextlib.suppress(OSError,ValueError,KeyError):
            saved=json.loads(cache.read_text())
            if saved['mac']==mac:hints.append(saved['ip'])
        if config.get('host'):hints.append(config['host'])
        with contextlib.suppress(OSError):
            for line in Path('/proc/net/arp').read_text().splitlines()[1:]:
                f=line.split()
                if len(f)>=4 and f[3].lower()==mac:hints.append(f[0])
    errors=[]
    for interface,ip,source,network in interfaces():
        try:
            with socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0806)) as s:
                s.bind((interface,0));s.setblocking(False)
                def wait(seconds):
                    end=time.monotonic()+seconds
                    while time.monotonic()<end:
                        if not select.select([s],[],[],max(0,end-time.monotonic()))[0]:break
                        frame=s.recv(65535)
                        found=response_ip(frame,target,source,ip,network)
                        if found:return found
                candidates=[]
                for hint in dict.fromkeys(hints):
                    with contextlib.suppress(ValueError):
                        if ipaddress.ip_address(hint) in network:candidates.append(hint)
                for address in candidates:s.send(request_frame(source,ip,address))
                found=wait(.6) if candidates else None
                for attempt in range(2):
                    if found:break
                    for address in network.hosts():
                        if str(address)==ip:continue
                        s.send(request_frame(source,ip,str(address)));time.sleep(.002)
                    found=wait(2)
                if found:
                    cache.parent.mkdir(parents=True,exist_ok=True)
                    tmp=cache.with_suffix('.tmp');tmp.write_text(json.dumps({'mac':mac,'ip':found,'interface':interface,'seen':time.time()}));tmp.replace(cache)
                    return found
        except PermissionError as e:
            raise DiscoveryError('區網探索需要 Docker host 網路與 NET_RAW 權限') from e
        except OSError as e:errors.append(interface+': '+str(e))
    raise DiscoveryError('區網找不到答錄機 '+mac+(' ('+'; '.join(errors)+')' if errors else '，請確認電源、網路線及與 NAS 位於同一區網'))
