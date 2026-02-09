import numpy as np
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt
import folium
# 1. DATA: Your grocery store coordinates (Lat, Long)
# Replace this with your actual list of 50-60 points
locations_with_names = [
    ([43.5535479, -80.2348729], "Angelino's Fresh Choice Market - 16 Stevenson St S"),
    ([43.5448674, -80.2528323], "Market Fresh - 10 Paisley St"),
    ([43.5516773, -80.2211858], "Ethnic Supermarket - 234 Victoria Rd S"),
    ([43.5420079, -80.2444236], "Goodness Me! Natural Food Market - 36 Wellington St W"),
    ([43.5452532, -80.2888985], "Than Phat Asian Grocer - 252 Silvercreek Pkwy N"),
    ([43.5433014, -80.2846749], "Guelph Butcher & Grocers (Asian Food Land) - 219 Silvercreek Pkwy N"),
    ([43.5467217, -80.249485], "Jans Guelph Latin Market - 101 Wyndham St N (Unit 1)"),
    ([43.5414159, -80.2487314], "SAFA Middle Eastern Market - 16 Essex St"),
    ([43.54523, -80.2498769], "Trotters Butcher Shop & Market - 42 Cork St E"),
    ([43.5405126, -80.2828799], "The Indian Supermarket - 170 Silvercreek Pkwy N"),
    ([43.5421582, -80.2899005], "India Spice House - 336 Speedvale Ave W"),
    ([43.5329643, -80.2878401], "Quality Indian Foods & Spices - 500 Willow Rd (Unit 8)"),
    ([43.5446701, -80.260488], "Valeriote's Market & Butchery - 204 Yorkshire St N"),
    ([43.5445286, -80.2520597], "The Stone Store - 14 Commercial St"),
    ([43.5470321, -80.2496353], "The Flour Barrel - 115 Wyndham St N"),
    ([43.5206265, -80.211144], "Rowe Farms - 1027 Gordon St"),
    ([43.521918, -80.212531], "University Square Bakery & Deli - 987 Gordon St"),
    ([43.5536731, -80.2317836], "Bella Roma Foods - 37 Empire St"),
    ([43.5447035, -80.2384397], "Sugo Mercato - 60 Ontario St"),
    ([43.5491387, -80.2941626], "Fresh Box Market - 410 Silvercreek Pkwy N"),
    ([43.5969091, -80.2098592], "Wellington Country Marketplace - 5259 Jones Baseline"),
    ([43.4759953, -80.1571218], "Strom's Farm & Bakery - 5089 Wellington Rd 32"),
]

locations = np.array([loc for loc, name in locations_with_names])
store_names = [name for loc, name in locations_with_names]


# 2. CONFIG
num_facilities = 2

# 3. ALGORITHM (K-Means still finds the best "centers" based on density)
kmeans = KMeans(n_clusters=num_facilities, random_state=0, n_init='auto')
kmeans.fit(locations)
labels = kmeans.labels_
colors = ['blue', 'green', 'purple', 'orange'][:num_facilities]
optimal_centers = kmeans.cluster_centers_
# Create base map centered on Ontario
m = folium.Map(location=[43.549999, -80.250000], zoom_start=12)

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

    folium.Circle(
        location=[center[0], center[1]],
        radius=5000,
        color=colors[i],
        fill=False,
        weight=2
    ).add_to(m)

m.save('interactive_hub_map_Guelph.html')

#------------------------------------------------------#
#                      EXTRAS                          #
#------------------------------------------------------#

# 4. PLOTTING - MODIFIED FOR "CONNECT TO ALL"

# # FIGURE A: Distance from EACH Facility to ALL Stores
# for i in range(num_facilities):
#     plt.figure(figsize=(8, 6))
    
#     center = optimal_centers[i]
    
#     # 1. Plot the Hub
#     plt.scatter(center[1], center[0], c='red', s=250, marker='*', zorder=10, label=f'Facility {i+1}')
    
#     # 2. Plot ALL Stores (Not just the closest ones)
#     plt.scatter(locations[:, 1], locations[:, 0], c='blue', s=100, label='Grocery Stores')
    
#     # 3. Draw lines to ALL Stores
#     for store_loc in locations:
#         plt.plot([center[1], store_loc[1]], [center[0], store_loc[0]], 'k--', alpha=0.3)
    
#     plt.title(f'Facility {i+1} Connected to ALL Stores')
#     plt.xlabel('Longitude')
#     plt.ylabel('Latitude')
#     plt.legend()
#     plt.grid(True, linestyle='--', alpha=0.7)
#     plt.show()

# # FIGURE B: Summary Map (No lines, just showing where everything is)
# plt.figure(figsize=(10, 8))
# plt.scatter(locations[:, 1], locations[:, 0], c='blue', s=100, label='Grocery Stores')
# plt.scatter(optimal_centers[:, 1], optimal_centers[:, 0], c='red', s=300, marker='X', label='Optimal Hubs')
# plt.title('Map of All Stores and 4 Optimal Hub Locations')
# plt.xlabel('Longitude')
# plt.ylabel('Latitude')
# plt.legend()
# plt.grid(True, linestyle='--', alpha=0.7)
# plt.show()
