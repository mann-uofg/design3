import numpy as np
from sklearn.cluster import KMeans
import folium
# 1. DATA: Your grocery store coordinates (Lat, Long)


## Reference for coordinates : [https://www.maps.ie/coordinates.html](https://www.maps.ie/coordinates.html)
# Replace this with your actual list of 50-60 points
locations_raw = [
    ([43.5161018,-80.2369272], "Farm Boy Guelph: 370 Stone Road West"),
    ([43.4209679, -80.4404296], "Farm Boy Kitchener: 385 Fairway Road South"),
    ([43.4855568, -80.5274827], "Farm Boy Waterloo: 417 King Street North"),
    ([43.3935986, -80.3206588], "Farm Boy Cambridge: 350 Hespeler Road"),
    ([42.9361714, -81.2243313], "Farm Boy Wellington (London): 1045 Wellington Road"),
    ([42.9375874,-81.2258503], "Farm Boy Masonville (London): 109 Fanshawe Park Road East"),
    ([42.9894097, -81.2980569], "Farm Boy Beaverbrook (London): 1415 Beaverbrook Avenue"),
    ([43.1773159, -80.2782687], "Farm Boy Brantford: 240 King George Road"),
    ([43.156195, -79.2715741], "Farm Boy St. Catharines: 295 Fourth Avenue"),
    ([43.2331723, -79.9235966], "Farm Boy Hamilton: 801 Mohawk Road West"),
    ([43.3518338, -79.7886164], "Farm Boy Burlington: 3061 Walkers Line"),
    ([43.3934668, -79.8247607], "Farm Boy Burlington South: 3230 Fairview Street"),
    ([43.4925476, -79.6798627], "Farm Boy Oakville: 1907 Ironoak Way"),
    ([43.3958128, -79.7113725], "Farm Boy Bronte (Oakville): 2441 Lakeshore Road West"),
    ([43.5469996, -79.590122], "Farm Boy Port Credit (Mississauga): 175 Lakeshore Road West"),
    ([43.6603152, -79.3844864], "Farm Boy College & Bay: 777 Bay Street"),
    ([43.6410409, -79.4012546], "Farm Boy Front & Bathurst: 29 Bathurst Street"),
    ([43.6394101, -79.3805122], "Farm Boy Harbourfront: 207 Queens Quay West"),
    ([43.6437105, -79.3710502], "Farm Boy Sugar Wharf: 100 Queens Quay East"),
    ([43.6714265, -79.4235408], "Farm Boy Dupont: 744 Dupont Street"),
    ([43.688501, -79.3907567], "Farm Boy St. Clair: 81 St. Clair Avenue East"),
    ([43.7042642, -79.3972232], "Farm Boy Yonge & Soudan: 2149 Yonge Street"),
    ([43.7096892, -79.3595562], "Farm Boy Leaside: 147 Laird Drive"),
    ([43.6578398, -79.3303826], "Farm Boy Leslieville: 1005 Lake Shore Boulevard East"),
    ([43.6100852, -79.5475302], "Farm Boy Alderwood (Etobicoke): 841 Brown's Line"),
    ([43.6385451, -79.537566], "Farm Boy Aukland (Etobicoke): 5245 Dundas Street West"),
    ([43.8466334, -79.3799697], "Farm Boy Shoppes of the Parkway (Richmond Hill): 65-95 East Beaver Creek"),
    ([43.9267897, -79.4514569], "Farm Boy Yonge & Silver Maple (Richmond Hill): 12276 Yonge Street"),
    ([44.0108058, -79.4165102], "Farm Boy Aurora: 10 Goulding Avenue"),
    ([44.0703676, -79.4840083], "Farm Boy Newmarket: 18075 Yonge Street"),
    ([44.33530435764122, -79.69064637640413], "Farm Boy Barrie: 436 Bryne Drive"),
    ([43.8352767, -79.0887562], "Farm Boy Pickering: 1355 Kingston Road"),
    ([43.9189828, -78.9415263], "Farm Boy Whitby: 360 Taunton Road East"),
    ([43.9364041, -78.8553646], "Farm Boy Oshawa: 1280 Clearbrook Drive"),
    ([44.257313, -76.5536571], "Farm Boy Kingston: 940 Futures Gate"),
    ([45.0278916, -74.7328714], "Farm Boy Cornwall: 814 Sydney Street (The Original Store)"),
    ([45.3457988, -75.6260126], "Farm Boy Bank Street (Blue Heron): 1500 Bank Street"),
    ([45.288643, -75.7240977], "Farm Boy Barrhaven: 3033 Woodroffe Avenue"),
    ([45.3457988, -75.6260126], "Farm Boy Blossom Park: 2950 Bank Street"),
    ([45.3629928, -75.7915505], "Farm Boy Britannia: 1495 Richmond Road"),
    ([45.2741712, -75.7470098], "Farm Boy Greenbank: 1581 Greenbank Road"),
    ([45.3107805, -75.9216603], "Farm Boy Kanata: 700 Terry Fox Drive"),
    ([45.4430895, -75.6447295], "Farm Boy Montreal Road (Hillside): 585 Montreal Road"),
    ([45.4777309, -75.5132795], "Farm Boy Orleans: 3035 St. Joseph Boulevard"),
    ([45.418746, -75.6933872], "Farm Boy Rideau: 193 Metcalfe Street"),
    ([45.3107805, -75.9216603], "Farm Boy Signature Centre (Kanata): 499 Terry Fox Drive"),
    ([45.2662373, -75.9414901], "Farm Boy Stittsville: 6315 Hazeldean Road"),
    ([45.4582935, -75.4878897], "Farm Boy Tenth Line (Orleans): 2030 Tenth Line Road"),
    ([45.4181185, -75.6469296], "Farm Boy Train Yards: 830 Belfast Road"),
    ([45.3896623, -75.6137995], "Farm Boy Walkley: 1980 Walkley Road"),
    ([45.3962011, -75.7499293], "Farm Boy Westboro: 317 McRae Avenue"),
    ([45.3464283, -75.73225], "Farm Boy Merivale: 1642 Merivale Road"),
]

locations = np.array([loc for loc, name in locations_raw])
store_names = [name for loc, name in locations_raw]


# 2. CONFIG
num_facilities = 4


# 3. ALGORITHM (K-Means still finds the best "centers" based on density)
kmeans = KMeans(n_clusters=num_facilities, random_state=0, n_init='auto')
kmeans.fit(locations)
labels = kmeans.labels_
colors = ['blue', 'green', 'purple', 'orange'][:num_facilities]
optimal_centers = kmeans.cluster_centers_

# Create base map centered on Ontario
m = folium.Map(location=[44.5, -79.5], zoom_start=7)

# Add markers for each cluster with different colors
for i in range(num_facilities):
    cluster_points = locations[labels == i]
    cluster_indices = np.where(labels == i)[0]
    for idx, point in zip(cluster_indices, cluster_points):
        folium.CircleMarker(
            location=[point[0], point[1]],
            radius=6,
            popup=store_names[idx],
            tooltip=store_names[idx],
            color=colors[i],
            fill=True,
            fillColor=colors[i]
        ).add_to(m)

# Add hub markers
for i, center in enumerate(optimal_centers):
    folium.Marker(
        location=[center[0], center[1]],
        popup=f'Hub {i+1}',
        icon=folium.Icon(color='red', icon='star', prefix='fa')
    ).add_to(m)
    
    # Add coverage circles
    folium.Circle(
        location=[center[0], center[1]],
        radius=50000,  # 50km in meters
        color=colors[i],
        fill=False,
        weight=2
    ).add_to(m)

m.save('interactive_hub_map_FarmBoy.html')


#------------------------------------------------------#
#                      EXTRAS                          #
#------------------------------------------------------#

# FIGURE B: Summary Map (No lines, just showing where everything is)
# plt.figure(figsize=(10, 8))
# plt.scatter(locations[:, 1], locations[:, 0], c='blue', s=100, label='Grocery Stores')
# plt.scatter(optimal_centers[:, 1], optimal_centers[:, 0], c='red', s=300, marker='X', label='Optimal Hubs')
# plt.title('Map of All Stores and 4 Optimal Hub Locations')
# plt.xlabel('Longitude')
# plt.ylabel('Latitude')
# plt.legend()
# plt.grid(True, linestyle='--', alpha=0.7)
# plt.show()
